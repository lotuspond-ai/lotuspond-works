from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from time import monotonic

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from lotuspond.execution.authorization import ExecutionActor, authorize_execution, reauthorize
from lotuspond.execution.errors import ExecutionError
from lotuspond.models import ApiKey, Membership, Workspace, new_id, now
from lotuspond.security import Principal

from .test_publication import harness as harness


def test_key_creator_demotion_after_lock_wait(harness):
    h = harness
    key_id = new_id()
    with Session(h.engine) as db, db.begin():
        db.add(
            ApiKey(
                id=key_id,
                workspace_id=h.scope.workspace_id,
                name="test",
                prefix="test",
                token_hash="1" * 64,
                scopes=["execute"],
                expires_at=now() + timedelta(days=1),
                created_by_user_id=h.principal.user_id,
            )
        )
    started = Event()
    pid = []

    def blocked():
        with Session(h.engine) as db, db.begin():
            pid.append(db.scalar(text("SELECT pg_backend_pid()")))
            started.set()
            try:
                authorize_execution(
                    db, Principal(key_id, h.scope.workspace_id, "service"), h.scope, "execute"
                )
            except ExecutionError as error:
                return error
            pytest.fail("A demoted key creator acquired execution authority")

    with ThreadPoolExecutor(max_workers=1) as executor:
        with Session(h.engine) as blocker, blocker.begin():
            blocker.scalar(
                select(Workspace).where(Workspace.id == h.scope.workspace_id).with_for_update()
            )
            future = executor.submit(blocked)
            assert started.wait(5)
            deadline = monotonic() + 5
            with h.engine.connect() as observer:
                while monotonic() < deadline:
                    if observer.scalar(
                        text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"), {"pid": pid[0]}
                    ):
                        break
                else:
                    pytest.fail("Worker never reached the real PostgreSQL lock barrier")
            blocker.get(Membership, (h.scope.workspace_id, h.principal.user_id)).role = "viewer"
        demoted_key_error = future.result(timeout=5)
    assert demoted_key_error.code == "forbidden"


def test_actor_dto_does_not_supply_authority(harness):
    h = harness
    actor = ExecutionActor(h.principal.user_id, None, h.scope, "owner")
    with Session(h.engine) as db, db.begin(), pytest.raises(ExecutionError, match="forbidden"):
        reauthorize(db, actor, "manage")


@pytest.mark.parametrize("operation", ["manage", "human_form", "reconcile"])
def test_key_cannot_manage_or_make_human_decisions(harness, operation):
    h = harness
    with Session(h.engine) as db, db.begin():
        key = ApiKey(
            workspace_id=h.scope.workspace_id,
            name="test",
            prefix="test",
            token_hash="2" * 64,
            scopes=["execute", "execution_signal", "write"],
            expires_at=now() + timedelta(days=1),
            created_by_user_id=h.principal.user_id,
        )
        db.add(key)
        db.flush()
        with pytest.raises(ExecutionError, match="forbidden"):
            authorize_execution(
                db, Principal(key.id, h.scope.workspace_id, "service"), h.scope, operation
            )


def test_current_write_key_can_publish_but_cannot_manage(harness):
    from lotuspond_sdk.executable.signing import sign_executable_package

    from lotuspond.execution.contracts import PublicationRequest
    from lotuspond.execution.publication import publish_v2
    from lotuspond.models import ExecutablePublication

    from .test_publication import example

    h = harness
    with Session(h.engine) as db, db.begin():
        db.get(Membership, (h.scope.workspace_id, h.principal.user_id)).role = "editor"
        key = ApiKey(
            workspace_id=h.scope.workspace_id,
            name="test",
            prefix="test",
            token_hash="3" * 64,
            scopes=["write"],
            expires_at=now() + timedelta(days=1),
            created_by_user_id=h.principal.user_id,
        )
        db.add(key)
        db.flush()
        principal = Principal(key.id, h.scope.workspace_id, "service")
        actor = authorize_execution(db, principal, h.scope, "publish")
        raw = example("minimal")
        request = PublicationRequest(
            package=raw,
            dependency_ids=(),
            signature=sign_executable_package(raw, h.key).model_dump(),
        )
        publication = publish_v2(db, actor, request, "key", dependencies=h.dependencies)
        assert db.get(ExecutablePublication, publication.id).publisher_api_key_id == key.id
        with pytest.raises(ExecutionError, match="forbidden"):
            reauthorize(db, actor, "manage")
        key.revoked_at = now()
        db.flush()
        with pytest.raises(ExecutionError, match="forbidden"):
            publish_v2(db, actor, request, "key", dependencies=h.dependencies)


@pytest.mark.parametrize("invalid", ["no_write", "viewer", "revoked", "expired", "run_not_execute"])
def test_current_key_constraints_are_not_inferred_from_old_principal(harness, invalid):
    h = harness
    with Session(h.engine) as db, db.begin():
        key = ApiKey(
            workspace_id=h.scope.workspace_id,
            name="test",
            prefix="test",
            token_hash="4" * 64,
            scopes=["write", "run"],
            expires_at=now() + timedelta(days=1),
            created_by_user_id=h.principal.user_id,
        )
        if invalid == "no_write":
            key.scopes = ["execute"]
        elif invalid == "viewer":
            db.get(Membership, (h.scope.workspace_id, h.principal.user_id)).role = "viewer"
        elif invalid == "revoked":
            key.revoked_at = now()
        elif invalid == "expired":
            key.expires_at = now() - timedelta(seconds=1)
        db.add(key)
        db.flush()
        with pytest.raises(ExecutionError, match="forbidden"):
            authorize_execution(
                db,
                Principal(key.id, h.scope.workspace_id, "owner"),
                h.scope,
                "execute" if invalid == "run_not_execute" else "publish",
            )
