"""Task 12 harness: the real API over the Task 11 harness, with sessions, keys and a runtime.

The app is built with the harness's own services (test mode only). Customer
sessions and API keys are rows for the harness's users; the runtime keeps the
harness's instance and session, with real tokens so it authenticates over HTTP.
"""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from lotuspond.app import create_app
from lotuspond.config import Settings
from lotuspond.execution.artifacts import ArtifactStore
from lotuspond.execution.boundary import ExecutionServices
from lotuspond.execution.context import ContextSource
from lotuspond.execution.inbox import InboxService
from lotuspond.fleet.authentication import new_token as runtime_token
from lotuspond.fleet.authentication import token_digest
from lotuspond.models import (
    ApiKey,
    LoginSession,
    Membership,
    RuntimeCredential,
    RuntimeSession,
    User,
    new_id,
)
from lotuspond.security import digest, new_token, now

from .test_artifacts import FixtureAccess


def api_settings(**changes):
    return Settings(
        **{
            "env": "test",
            "database_url": "sqlite://",
            "origin": "http://testserver",
            "redis_url": None,
            "fleet_enabled": True,
            "fleet_installation_id": "test-installation",
            **changes,
        }
    )


def login(h, user_id):
    """A customer browser session for this user in the harness workspace."""
    token = new_token()
    with Session(h.engine) as db, db.begin():
        db.add(
            LoginSession(
                user_id=user_id, token_hash=digest(token), expires_at=now() + timedelta(hours=1)
            )
        )
    return {"cookie": f"lotuspond_session={token}", "x-lotuspond-workspace": h.scope.workspace_id}


def member(h, role):
    """Another member of the harness workspace, with that role; their user ID."""
    user_id = new_id()
    with Session(h.engine) as db, db.begin():
        db.add(User(id=user_id, email=f"{user_id}@example.com", name=role, password_hash="x"))
        db.flush()
        db.add(Membership(workspace_id=h.scope.workspace_id, user_id=user_id, role=role))
    return user_id


def key(h, scopes, user_id=None):
    """An API key with these scopes, created by this user (the owner by default)."""
    raw = f"lp_{new_token()}"
    with Session(h.engine) as db, db.begin():
        db.add(
            ApiKey(
                id=new_id(),
                workspace_id=h.scope.workspace_id,
                name="test",
                scopes=sorted(scopes),
                token_hash=digest(raw),
                prefix=raw[:11],
                created_by_user_id=user_id or h.actor.user_id,
                expires_at=now() + timedelta(days=1),
            )
        )
    return {"authorization": f"Bearer {raw}"}


def runtime_headers(h):
    """Real tokens for the harness's runtime credential and its current session."""
    token, secret = runtime_token("lpr_"), runtime_token("lps_")
    principal = h.principal_runtime
    with Session(h.engine) as db, db.begin():
        db.get(RuntimeCredential, principal.credential_id).token_digest = token_digest(
            token, "lpr_"
        )
        db.get(RuntimeSession, principal.session_id).token_digest = token_digest(secret, "lps_")
    return {"authorization": f"Bearer {token}", "x-lotuspond-runtime-session": secret}


@pytest.fixture
def api(controlled):
    h = controlled
    h.inbox = InboxService(budgets=h.budgets, artifacts=h.store, artifact_policy=h.policy)
    # Context sources need a current source-access provider; the same keys serve both.
    h.access = FixtureAccess()
    h.reads = ArtifactStore(h.keys, h.access, test_mode=True)
    h.context = ContextSource(h.reads)
    h.services = ExecutionServices(
        artifacts=h.reads,
        artifact_policy=h.policy,
        budgets=h.budgets,
        dependencies=h.dependencies,
        admission=h.service,
        inbox=h.inbox,
        controls=h.controls,
        activities=h.activities,
        leases=h.leases,
        reconciliation=h.reconciliation,
        context=h.context,
    )
    application = create_app(
        api_settings(), engine=h.engine, fleet_dependencies=h.fleet, execution_services=h.services
    )
    with TestClient(application, headers={"origin": "http://testserver"}) as client:
        h.client = client
        h.M = f"/api/v1/projects/{h.scope.project_id}/environments/{h.scope.environment_id}"
        h.owner = login(h, h.actor.user_id)
        h.runtime = runtime_headers(h)
        yield h
    h.reads.close()
