from datetime import timedelta

import redis
from pydantic import SecretStr
from sqlalchemy import select

from lotuspond.models import (
    AlertDelivery,
    Assessment,
    EvaluationJob,
    IdentityChallenge,
    Incident,
    IncidentEvent,
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
    assert body["workers"]["evaluation"]["observation"]["state"] == "not_configured"
    assert body["workers"]["mail"]["observation"]["state"] == "disabled"
    assert body["workers"]["alert"]["observation"]["state"] == "disabled"
    assert body["queues"]["mail"]["enabled"] is False
    assert body["queues"]["alert"]["enabled"] is False
    assert body["assessments_last_24h"] == {"failed": 0, "unknown": 0}


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

        db.add_all(
            [
                EvaluationJob(
                    workspace_id=account,
                    project_id=active_project.id,
                    status="queued",
                    criteria="private criteria queued",
                    created_at=current - timedelta(minutes=20),
                ),
                EvaluationJob(
                    workspace_id=account,
                    project_id=active_project.id,
                    status="running",
                    criteria="private criteria leased",
                    attempts=1,
                    lease_expires_at=current - timedelta(minutes=2),
                    lease_token_hash=digest("expired-evaluation-lease"),
                    created_at=current - timedelta(minutes=10),
                ),
                EvaluationJob(
                    workspace_id=suspended_workspace.id,
                    project_id=suspended_project.id,
                    status="queued",
                    criteria="private suspended criteria",
                ),
            ]
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

        active_incident = Incident(
            workspace_id=account,
            project_id=active_project.id,
            title="private incident",
            severity="medium",
            category="manual",
            source="manual",
            status="open",
        )
        suspended_incident = Incident(
            workspace_id=suspended_workspace.id,
            project_id=suspended_project.id,
            title="private suspended incident",
            severity="medium",
            category="manual",
            source="manual",
            status="open",
        )
        db.add_all([active_incident, suspended_incident])
        db.flush()
        active_event = IncidentEvent(
            workspace_id=account,
            incident_id=active_incident.id,
            action="created",
            actor_id="system",
        )
        suspended_event = IncidentEvent(
            workspace_id=suspended_workspace.id,
            incident_id=suspended_incident.id,
            action="created",
            actor_id="system",
        )
        db.add_all([active_event, suspended_event])
        db.flush()
        db.add_all(
            [
                AlertDelivery(
                    workspace_id=account,
                    incident_id=active_incident.id,
                    incident_event_id=active_event.id,
                    destination="operator",
                    event="incident.opened",
                    payload={"event_id": "private-event"},
                    status="queued",
                    available_at=current - timedelta(minutes=1),
                    created_at=current - timedelta(minutes=12),
                ),
                AlertDelivery(
                    workspace_id=account,
                    incident_id=active_incident.id,
                    incident_event_id=active_event.id,
                    destination="delayed",
                    event="incident.opened",
                    payload={"event_id": "private-delayed"},
                    status="queued",
                    available_at=current + timedelta(minutes=5),
                ),
                AlertDelivery(
                    workspace_id=account,
                    incident_id=active_incident.id,
                    incident_event_id=active_event.id,
                    destination="expired",
                    event="incident.opened",
                    payload={"event_id": "private-expired"},
                    status="sending",
                    attempts=1,
                    available_at=current - timedelta(minutes=5),
                    lease_expires_at=current - timedelta(minutes=1),
                    lease_token_hash=digest("expired-alert-lease"),
                ),
                AlertDelivery(
                    workspace_id=suspended_workspace.id,
                    incident_id=suspended_incident.id,
                    incident_event_id=suspended_event.id,
                    destination="suspended",
                    event="incident.opened",
                    payload={"event_id": "private-suspended"},
                    status="queued",
                    available_at=current - timedelta(minutes=1),
                ),
            ]
        )
        db.add_all(
            [
                Assessment(
                    workspace_id=account,
                    project_id=active_project.id,
                    status="failed",
                    criteria="private failed evidence",
                    reason="private failure reason",
                    provider="test",
                    created_at=current - timedelta(hours=1),
                ),
                Assessment(
                    workspace_id=account,
                    project_id=active_project.id,
                    status="unknown",
                    criteria="private unavailable evidence",
                    reason="private unavailable reason",
                    provider="test",
                    created_at=current - timedelta(hours=2),
                ),
                Assessment(
                    workspace_id=account,
                    project_id=active_project.id,
                    status="failed",
                    criteria="too old",
                    reason="too old",
                    provider="test",
                    created_at=current - timedelta(hours=25),
                ),
                Assessment(
                    workspace_id=account,
                    project_id=active_project.id,
                    status="unknown",
                    criteria="future",
                    reason="future",
                    provider="test",
                    created_at=current + timedelta(hours=1),
                ),
            ]
        )

    response = client.get("/api/v1/admin/operations")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["queues"]["evaluation"]["eligible_now"] == 2
    assert body["queues"]["evaluation"]["suspended"] == 1
    assert body["queues"]["evaluation"]["expired_leases"] == 1
    assert body["queues"]["evaluation"]["oldest_eligible_age_seconds"] >= 1190
    assert body["queues"]["mail"]["eligible_now"] == 2
    assert body["queues"]["mail"]["delayed"] == 1
    assert body["queues"]["mail"]["suspended"] == 1
    assert body["queues"]["mail"]["expired_leases"] == 1
    assert body["queues"]["alert"]["eligible_now"] == 2
    assert body["queues"]["alert"]["delayed"] == 1
    assert body["queues"]["alert"]["suspended"] == 1
    assert body["queues"]["alert"]["expired_leases"] == 1
    assert body["assessments_last_24h"] == {"failed": 1, "unknown": 1}
    assert body["window"]["duration_seconds"] == 86400
    serialized = response.text
    for private in (
        "Private project name",
        "private criteria",
        "private incident",
        "private failure reason",
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
    assert mark_poll(fake, "evaluation") is True

    fresh = client.get("/api/v1/admin/operations").json()
    assert fresh["dependencies"]["redis"]["state"] == "ok"
    observation = fresh["workers"]["evaluation"]["observation"]
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
    assert unavailable["workers"]["evaluation"]["observation"]["state"] == (
        "unavailable"
    )
