"""E05 Task 11 harness: the real API over the action harness, with sessions and a runtime.

The app is built with the harness's own services (test mode only), including the
action services. Customer sessions and keys are rows for harness users; the action
runtime keeps its instance and session, with real tokens so it authenticates over
HTTP. `HttpTransport` stands in for the runtime's TLS control transport, so the
runtime's own `ActionClient` meets the real routes.
"""

import pytest
from fastapi.testclient import TestClient
from lotuspond_runtime.action_client import ActionClient
from lotuspond_runtime.control_transport import _STATUS
from lotuspond_runtime.errors import FleetError
from pydantic import SecretStr
from sqlalchemy.orm import Session

from lotuspond.app import create_app
from lotuspond.execution.action_projections import ActionProjection
from lotuspond.execution.boundary import ActionServices, ExecutionServices
from lotuspond.execution.context import ContextSource
from lotuspond.fleet.authentication import new_token as runtime_token
from lotuspond.fleet.authentication import token_digest
from lotuspond.models import RuntimeCredential, RuntimeSession

from ..api_fixtures import api_settings, login, member


class HttpTransport:
    """The runtime's control transport, over the API test client instead of TLS."""

    def __init__(self, client):
        self.client = client

    def request(self, method, route, body, token, session):
        headers = {
            "authorization": "Bearer " + token.get_secret_value(),
            "content-type": "application/json",
        }
        if session is not None:
            headers["x-lotuspond-runtime-session"] = session.get_secret_value()
        response = self.client.request(
            method, "/api/v1/runtime" + route, content=body, headers=headers
        )
        if response.status_code != 200:
            raise FleetError(_STATUS.get(response.status_code, "transport_failed"))
        return response.content

    def close(self):
        pass


def runtime_headers(h, principal=None):
    """Real tokens for an action runtime's credential and its current session."""
    principal = principal or h.runtime
    token, secret = runtime_token("lpr_"), runtime_token("lps_")
    with Session(h.engine) as db, db.begin():
        db.get(RuntimeCredential, principal.credential_id).token_digest = token_digest(
            token, "lpr_"
        )
        db.get(RuntimeSession, principal.session_id).token_digest = token_digest(secret, "lps_")
    return {"authorization": f"Bearer {token}", "x-lotuspond-runtime-session": secret}


def action_client(h, headers=None):
    headers = headers or h.wire
    token = headers["authorization"].removeprefix("Bearer ")
    session = headers["x-lotuspond-runtime-session"]
    return ActionClient(HttpTransport(h.client), SecretStr(token), SecretStr(session))


def session_for(h, role):
    """A browser session for a new member with this role: (user ID, headers)."""
    user_id = member(h, role)
    return user_id, login(h, user_id)


@pytest.fixture
def action_api(resolving):
    h = resolving
    h.services = ExecutionServices(
        artifacts=h.store,
        artifact_policy=h.policy,
        budgets=h.budgets,
        dependencies=h.dependencies,
        admission=h.service,
        inbox=h.inbox,
        controls=h.controls,
        activities=h.activities,
        leases=h.leases,
        reconciliation=h.reconciliation,
        context=ContextSource(h.store),
        actions=ActionServices(
            tasks=h.tasks,
            projection=ActionProjection(),
            resolutions=h.resolutions,
            dispatcher=h.dispatcher,
        ),
    )
    application = create_app(
        api_settings(), engine=h.engine, fleet_dependencies=h.fleet, execution_services=h.services
    )
    with TestClient(application, headers={"origin": "http://testserver"}) as client:
        h.client = client
        h.M = f"/api/v1/projects/{h.scope.project_id}/environments/{h.scope.environment_id}"
        h.owner = login(h, h.actor.user_id)
        h.wire = runtime_headers(h)
        yield h
