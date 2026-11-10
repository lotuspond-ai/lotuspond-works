from datetime import timedelta

import redis
from pydantic import SecretStr
from sqlalchemy import select

from lotuspond.models import (
    EvaluationJob,
    IdentityChallenge,
    Invitation,
    MailOutbox,
    Project,
    User,
    Workspace,
    new_id,
    now,
)
from lotuspond.security import digest
from lotuspond.worker_observation import mark_poll


class FakeRedis:
    def __init__(self, *, unavailable: bool = False):
        self.values = {}
        self.expiries = {}
        self.unavailable = unavailable

    def set(self, key, value, *, ex):
        if self.unavailable:
            raise redis.RedisError("unavailable")
        self.values[key] = value
        self.expiries[key] = ex
        return True

    def mget(self, keys):
        if self.unavailable:
            raise redis.RedisError("unavailable")
        return [self.values.get(key) for key in keys]

    def close(self):
        return None


def make_operator(client, db_factory):
    with db_factory.begin() as db:
        user = db.scalar(select(User).where(User.email == "owner@example.com"))
        user.is_platform_admin = True
        return user.id


def test_operations_requires_platform_operator_and_reports_optional_workers(
    client, account, db_factory
):
    denied = client.get("/api/v1/admin/operations")
    assert denied.status_code == 403
    make_operator(client, db_factory)

    response = client.get("/api/v1/admin/operations")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["dependencies"] == {
        "database": {"state": "ok", "error_code": None},
        "redis": {"state": "not_configured", "error_code": None},
    }
    # The assessment and alert workers are retired (ADR-0013); identity mail is the only queue.
    assert set(body["workers"]) == set(body["queues"]) == {"mail"}
    assert body["workers"]["mail"]["observation"]["state"] == "disabled"
    assert body["queues"]["mail"]["enabled"] is False
    assert "assessments_last_24h" not in body and "window" not in body


def test_operations_aggregates_bounded_queue_health_without_customer_evidence(
    client, app, account, db_factory
):
    user_id = make_operator(client, db_factory)
    current = now()
    with db_factory.begin() as db:
        active_project = Project(
            workspace_id=account,
            name="Private project name",
            description="private description",
        )
        suspended_workspace = Workspace(
            name="Suspended customer",
            slug=f"suspended-{new_id()[:8]}",
            kind="organization",
            owner_id=user_id,
            status="suspended",
        )
        db.add_all([active_project, suspended_workspace])
        db.flush()
        suspended_project = Project(
            workspace_id=suspended_workspace.id,
            name="Suspended project",
            description="",
        )
        db.add(suspended_project)
        db.flush()

        db.add(
            EvaluationJob(
                workspace_id=account,
                project_id=active_project.id,
                status="queued",
                criteria="private criteria queued",
                created_at=current - timedelta(minutes=20),
            )
        )
        challenge = IdentityChallenge(
            user_id=user_id,
            kind="email_verification",
            token_hash=digest("operations-mail-challenge"),
            expires_at=current + timedelta(hours=1),
        )
        db.add(challenge)
        db.flush()
        db.add_all(
            [
                MailOutbox(
                    workspace_id=account,
                    identity_challenge_id=challenge.id,
                    kind="email_verification",
                    recipient_hash=digest("recipient-one"),
                    body_ciphertext="encrypted",
                    status="queued",
                    available_at=current - timedelta(minutes=1),
                    created_at=current - timedelta(minutes=15),
                ),
                MailOutbox(
                    workspace_id=account,
                    identity_challenge_id=challenge.id,
                    kind="email_verification",
                    recipient_hash=digest("recipient-two"),
                    body_ciphertext="encrypted",
                    status="queued",
                    available_at=current + timedelta(minutes=5),
                ),
                MailOutbox(
                    workspace_id=account,
                    identity_challenge_id=challenge.id,
                    kind="email_verification",
                    recipient_hash=digest("recipient-three"),
                    body_ciphertext="encrypted",
                    status="sending",
                    attempts=1,
                    available_at=current - timedelta(minutes=5),
                    lease_expires_at=current - timedelta(minutes=1),
                    lease_token_hash=digest("expired-mail-lease"),
                ),
            ]
        )
        invitation = Invitation(
            workspace_id=suspended_workspace.id,
            email="suspended@example.com",
            role="viewer",
            token_hash=digest("operations-suspended-invitation"),
            created_by=user_id,
            expires_at=current + timedelta(days=1),
        )
        db.add(invitation)
        db.flush()
        db.add(
            MailOutbox(
                workspace_id=suspended_workspace.id,
                invitation_id=invitation.id,
                kind="invitation",
                recipient_hash=digest("recipient-suspended"),
                body_ciphertext="encrypted",
                status="queued",
                available_at=current - timedelta(minutes=1),
            )
        )

    response = client.get("/api/v1/admin/operations")
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body["queues"]) == {"mail"}
    assert body["queues"]["mail"]["eligible_now"] == 2
    assert body["queues"]["mail"]["delayed"] == 1
    assert body["queues"]["mail"]["suspended"] == 1
    assert body["queues"]["mail"]["expired_leases"] == 1
    serialized = response.text
    for private in (
        "Private project name",
        "private criteria",
        active_project.id,
    ):
        assert private not in serialized


def test_operations_reports_fresh_and_unavailable_redis_observation(
    client, app, account, db_factory
):
    make_operator(client, db_factory)
    fake = FakeRedis()
    app.state.settings.redis_url = SecretStr("redis://localhost:6379/0")
    app.state.limiter.client = fake
    app.state.settings.mail_enabled = True
    assert mark_poll(fake, "mail") is True

    fresh = client.get("/api/v1/admin/operations").json()
    assert fresh["dependencies"]["redis"]["state"] == "ok"
    observation = fresh["workers"]["mail"]["observation"]
    assert observation["state"] == "fresh"
    assert observation["last_poll_at"] is not None
    assert observation["age_seconds"] >= 0
    assert observation["fresh_for_seconds"] == 150

    fake.unavailable = True
    unavailable = client.get("/api/v1/admin/operations").json()
    assert unavailable["dependencies"]["redis"] == {
        "state": "unavailable",
        "error_code": "redis_unavailable",
    }
    assert unavailable["workers"]["mail"]["observation"]["state"] == "unavailable"
