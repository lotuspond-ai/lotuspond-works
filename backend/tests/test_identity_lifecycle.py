import base64
import re
import smtplib
from datetime import timedelta

import pytest
from cryptography.exceptions import InvalidTag
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from sqlalchemy import func, select

from lotuspond.config import Settings
from lotuspond.identity_status import identity_verification_source
from lotuspond.mail import (
    MailDeadline,
    MailDeadlineExceeded,
    claim_mail,
    decrypt_payload,
    deliver_claim,
    encrypt_payload,
)
from lotuspond.models import (
    AuditEvent,
    IdentityChallenge,
    Invitation,
    LoginSession,
    MailOutbox,
    Membership,
    OfflineEnrollment,
    OfflineIdentityAttestation,
    User,
    new_id,
    now,
)
from lotuspond.security import digest, password_hasher


def enable_mail(settings):
    settings.mail_enabled = True
    settings.mail_encryption_key = SecretStr(
        base64.urlsafe_b64encode(b"k" * 32).decode()
    )
    settings.smtp_host = "localhost"
    settings.smtp_port = 2525
    settings.smtp_from = "identity@lotuspond.example.com"
    settings.smtp_starttls = False


def token_from_latest(db_factory, settings, *, kind, user_id=None):
    with db_factory() as db:
        query = select(MailOutbox).where(MailOutbox.kind == kind)
        if user_id:
            query = query.join(
                IdentityChallenge,
                IdentityChallenge.id == MailOutbox.identity_challenge_id,
            ).where(IdentityChallenge.user_id == user_id)
        outbox = db.scalar(query.order_by(MailOutbox.created_at.desc(), MailOutbox.id.desc()))
        payload = decrypt_payload(settings, outbox.id, outbox.body_ciphertext)
        match = re.search(r"token=([A-Za-z0-9_-]+)", payload["body"])
        assert match
        assert match.group(1) not in outbox.body_ciphertext
        return match.group(1), outbox.id


def test_mailbox_verification_is_single_use_and_encrypted(
    client, account, app, db_factory
):
    enable_mail(app.state.settings)
    user_id = client.get("/api/v1/me").json()["user"]["id"]
    response = client.post("/api/v1/auth/verification/request", json={})
    assert response.status_code == 202
    token, outbox_id = token_from_latest(
        db_factory, app.state.settings, kind="email_verification", user_id=user_id
    )
    assert client.post("/api/v1/auth/verification/confirm", json={"token": token}).json() == {
        "ok": True
    }
    assert client.post(
        "/api/v1/auth/verification/confirm", json={"token": token}
    ).status_code == 400
    with db_factory() as db:
        assert db.get(User, user_id).email_verified is True
        outbox = db.get(MailOutbox, outbox_id)
        assert outbox.recipient_hash != "owner@example.com"
        events = list(
            db.scalars(select(AuditEvent).where(AuditEvent.resource_id == outbox_id))
        )
        assert all(token not in str(event.details) for event in events)


def test_expired_mailbox_challenge_is_denied(client, account, app, db_factory):
    enable_mail(app.state.settings)
    user_id = client.get("/api/v1/me").json()["user"]["id"]
    assert client.post("/api/v1/auth/verification/request", json={}).status_code == 202
    token, _ = token_from_latest(
        db_factory, app.state.settings, kind="email_verification", user_id=user_id
    )
    with db_factory.begin() as db:
        challenge = db.scalar(
            select(IdentityChallenge).where(
                IdentityChallenge.user_id == user_id,
                IdentityChallenge.kind == "email_verification",
            )
        )
        challenge.expires_at = now() - timedelta(seconds=1)
    response = client.post("/api/v1/auth/verification/confirm", json={"token": token})
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_token"


def test_password_reset_is_generic_revokes_sessions_and_invalidates_prior_tokens(
    app, client, account, db_factory
):
    enable_mail(app.state.settings)
    user_id = client.get("/api/v1/me").json()["user"]["id"]
    with TestClient(app, headers={"origin": "http://testserver"}) as other:
        assert other.post(
            "/api/v1/auth/login",
            json={"email": "owner@example.com", "password": "a-valid-test-password"},
        ).status_code == 200
        expected = {"status": "accepted"}
        assert client.post(
            "/api/v1/auth/password/reset/request", json={"email": "missing@example.com"}
        ).json() == expected
        assert client.post(
            "/api/v1/auth/password/reset/request", json={"email": "owner@example.com"}
        ).json() == expected
        first, _ = token_from_latest(
            db_factory, app.state.settings, kind="password_reset", user_id=user_id
        )
        assert client.post(
            "/api/v1/auth/password/reset/request", json={"email": "owner@example.com"}
        ).json() == expected
        second, _ = token_from_latest(
            db_factory, app.state.settings, kind="password_reset", user_id=user_id
        )
        assert client.post(
            "/api/v1/auth/password/reset/confirm",
            json={"token": first, "new_password": "a-new-valid-test-password"},
        ).status_code == 400
        assert client.post(
            "/api/v1/auth/password/reset/confirm",
            json={"token": second, "new_password": "a-new-valid-test-password"},
        ).json() == {"ok": True}
        assert client.get("/api/v1/me").status_code == 401
        assert other.get("/api/v1/me").status_code == 401
        assert client.post(
            "/api/v1/auth/password/reset/confirm",
            json={"token": second, "new_password": "another-valid-test-password"},
        ).status_code == 400
    with db_factory() as db:
        assert db.scalar(
            select(func.count()).select_from(LoginSession).where(LoginSession.user_id == user_id)
        ) == 0


def store_offline_identity(db_factory, email: str, *, completed: bool) -> str:
    """Store an operator-attested account, as offline enrollment left one before ADR-0014."""
    stored = now()
    with db_factory.begin() as db:
        user = User(
            id=new_id(),
            email=email,
            name="Offline recovery",
            password_hash=password_hasher.hash("offline-original-password"),
        )
        db.add(user)
        db.flush()
        attestation = OfflineIdentityAttestation(
            id=new_id(),
            user_id=user.id,
            source="in_person",
            source_reference="badge-recovery",
            operator_reference="operator-recovery",
            attested_at=stored,
        )
        db.add(attestation)
        db.flush()
        db.add(
            OfflineEnrollment(
                id=new_id(),
                user_id=user.id,
                attestation_id=attestation.id,
                token_hash=digest(f"enrollment-{user.id}"),
                created_at=stored,
                expires_at=stored + timedelta(days=1),
                consumed_at=stored if completed else None,
            )
        )
        return user.id


def test_stored_offline_identity_fails_closed_after_enrollment_was_retired(
    app, client, db_factory
):
    """Offline enrollment is retired (ADR-0014); its stored rows still bound each account."""
    pending = store_offline_identity(db_factory, "offline-pending@example.com", completed=False)
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "offline-pending@example.com", "password": "offline-original-password"},
    )
    assert login.status_code == 401
    assert client.post("/api/v1/auth/offline-enrollment/complete", json={}).status_code == 405

    completed = store_offline_identity(db_factory, "offline-recovery@example.com", completed=True)
    with db_factory() as db:
        # An operator attestation alone no longer verifies an identity.
        assert identity_verification_source(db, db.get(User, completed)) is None
        assert identity_verification_source(db, db.get(User, pending)) is None

    enable_mail(app.state.settings)
    expected = {"status": "accepted"}

    def reset_challenges(user_id):
        with db_factory() as db:
            return db.scalar(
                select(func.count())
                .select_from(IdentityChallenge)
                .where(
                    IdentityChallenge.user_id == user_id,
                    IdentityChallenge.kind == "password_reset",
                )
            )

    # A pending offline account cannot recover through its claimed mailbox,
    # even if its mailbox flag is set independently.
    with db_factory.begin() as db:
        db.get(User, pending).email_verified = True
    assert client.post(
        "/api/v1/auth/password/reset/request", json={"email": "offline-pending@example.com"}
    ).json() == expected
    assert reset_challenges(pending) == 0

    # A completed enrollment without mailbox ownership cannot recover either.
    assert client.post(
        "/api/v1/auth/password/reset/request", json={"email": "offline-recovery@example.com"}
    ).json() == expected
    assert reset_challenges(completed) == 0

    # Once both conditions hold, recovery is queued. Confirmation rechecks the
    # same boundary so a challenge cannot outlive mailbox eligibility.
    with db_factory.begin() as db:
        db.get(User, completed).email_verified = True
    with db_factory() as db:
        assert identity_verification_source(db, db.get(User, completed)) == "mailbox"
    assert client.post(
        "/api/v1/auth/password/reset/request", json={"email": "offline-recovery@example.com"}
    ).json() == expected
    reset_token, _ = token_from_latest(
        db_factory,
        app.state.settings,
        kind="password_reset",
        user_id=completed,
    )
    with db_factory.begin() as db:
        user = db.get(User, completed)
        original_hash = user.password_hash
        user.email_verified = False
    denied = client.post(
        "/api/v1/auth/password/reset/confirm",
        json={"token": reset_token, "new_password": "offline-reset-password"},
    )
    assert denied.status_code == 400
    assert denied.json()["error"] == "invalid_token"
    with db_factory() as db:
        assert db.get(User, completed).password_hash == original_hash


def test_invitation_allows_registration_then_requires_verified_matching_mailbox(
    app, client, account, db_factory
):
    enable_mail(app.state.settings)
    invitation = client.post(
        "/api/v1/invitations",
        json={"email": "invitee@example.com", "role": "editor", "days": 7},
    )
    assert invitation.status_code == 201
    assert invitation.json()["status"] == "pending"
    token, _ = token_from_latest(db_factory, app.state.settings, kind="invitation")
    app.state.settings.registration_enabled = False
    with TestClient(app, headers={"origin": "http://testserver"}) as invitee:
        assert invitee.post(
            "/api/v1/auth/register",
            json={
                "email": "other@example.com",
                "name": "Wrong mailbox",
                "password": "a-valid-test-password",
                "invitation_token": token,
            },
        ).status_code == 403
        registered = invitee.post(
            "/api/v1/auth/register",
            json={
                "email": "invitee@example.com",
                "name": "Invitee",
                "password": "a-valid-test-password",
                "invitation_token": token,
            },
        )
        assert registered.status_code == 201
        assert invitee.post(
            "/api/v1/invitations/accept", json={"token": token}
        ).status_code == 403
        user_id = invitee.get("/api/v1/me").json()["user"]["id"]
        assert invitee.post("/api/v1/auth/verification/request", json={}).status_code == 202
        verify_token, _ = token_from_latest(
            db_factory, app.state.settings, kind="email_verification", user_id=user_id
        )
        assert invitee.post(
            "/api/v1/auth/verification/confirm", json={"token": verify_token}
        ).status_code == 200
        accepted = invitee.post("/api/v1/invitations/accept", json={"token": token})
        assert accepted.json() == {"ok": True, "workspace_id": account, "role": "editor"}
        assert invitee.post(
            "/api/v1/invitations/accept", json={"token": token}
        ).status_code == 400
    assert client.post(
        "/api/v1/invitations",
        json={"email": "invitee@example.com", "role": "admin"},
    ).status_code == 409
    with db_factory() as db:
        user = db.scalar(select(User).where(User.email == "invitee@example.com"))
        assert db.get(Membership, (account, user.id)).role == "editor"


def test_invitations_refuse_personal_and_revoked_tokens(client, account, app, db_factory):
    enable_mail(app.state.settings)
    personal = next(
        item for item in client.get("/api/v1/me").json()["workspaces"] if item["kind"] == "personal"
    )
    assert client.post(
        "/api/v1/invitations",
        headers={"x-lotuspond-workspace": personal["id"]},
        json={"email": "person@example.com", "role": "viewer"},
    ).status_code == 409
    created = client.post(
        "/api/v1/invitations",
        json={"email": "revoked@example.com", "role": "viewer"},
    ).json()
    token, _ = token_from_latest(db_factory, app.state.settings, kind="invitation")
    assert client.post(f"/api/v1/invitations/{created['id']}/revoke", json={}).json() == {
        "ok": True
    }
    app.state.settings.registration_enabled = False
    with TestClient(app, headers={"origin": "http://testserver"}) as invitee:
        assert invitee.post(
            "/api/v1/auth/register",
            json={
                "email": "revoked@example.com",
                "name": "Revoked",
                "password": "a-valid-test-password",
                "invitation_token": token,
            },
        ).status_code == 403
    with db_factory() as db:
        assert db.get(Invitation, created["id"]).revoked_at is not None


class RecordingSMTP:
    instances = []
    fail = False

    def __init__(self, host, port, timeout):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.messages = []
        self.started_tls = False
        self.closed = False
        self.__class__.instances.append(self)

    def ehlo(self):
        return None

    def starttls(self, context):
        self.started_tls = True

    def login(self, username, password):
        return None

    def send_message(self, message):
        if self.fail:
            raise smtplib.SMTPException("fixture")
        self.messages.append(message)

    def quit(self):
        self.closed = True

    def close(self):
        self.closed = True


def test_mail_worker_sends_active_reference_and_clears_ciphertext(
    client, account, app, db_factory
):
    enable_mail(app.state.settings)
    app.state.settings.smtp_starttls = True
    assert client.post("/api/v1/auth/verification/request", json={}).status_code == 202
    with db_factory.begin() as db:
        claim = claim_mail(db)
    assert claim
    assert deliver_claim(
        db_factory, app.state.settings, claim, smtp_factory=RecordingSMTP
    ) is True
    with db_factory() as db:
        outbox = db.get(MailOutbox, claim.identifier)
        assert outbox.status == "sent"
        assert outbox.body_ciphertext == ""
        assert outbox.sent_at is not None
    assert RecordingSMTP.instances[-1].started_tls is True
    assert len(RecordingSMTP.instances[-1].messages) == 1


def test_mail_worker_cancels_inactive_and_terminalizes_expired_fifth_claim(
    client, account, app, db_factory
):
    enable_mail(app.state.settings)
    assert client.post("/api/v1/auth/verification/request", json={}).status_code == 202
    with db_factory.begin() as db:
        outbox = db.scalar(select(MailOutbox).order_by(MailOutbox.created_at.desc()))
        db.get(IdentityChallenge, outbox.identity_challenge_id).revoked_at = now()
    before = len(RecordingSMTP.instances)
    with db_factory.begin() as db:
        assert claim_mail(db) is None
    assert len(RecordingSMTP.instances) == before
    with db_factory.begin() as db:
        outbox = db.scalar(select(MailOutbox).order_by(MailOutbox.created_at.desc()))
        assert outbox.status == "cancelled"
        assert outbox.body_ciphertext == ""

        challenge = db.get(IdentityChallenge, outbox.identity_challenge_id)
        challenge.revoked_at = None
        challenge.expires_at = now() + timedelta(minutes=5)
        outbox.status = "sending"
        outbox.attempts = 5
        outbox.body_ciphertext = "fixture"
        outbox.lease_expires_at = now() - timedelta(seconds=1)
        outbox.lease_token_hash = "fixture"
    with db_factory.begin() as db:
        assert claim_mail(db) is None
    with db_factory() as db:
        outbox = db.scalar(select(MailOutbox).order_by(MailOutbox.created_at.desc()))
        assert outbox.status == "failed"
        assert outbox.last_error_code == "attempts_exhausted"
        assert outbox.body_ciphertext == ""


def test_mail_worker_refuses_an_expired_lease_before_smtp(client, account, app, db_factory):
    enable_mail(app.state.settings)
    assert client.post("/api/v1/auth/verification/request", json={}).status_code == 202
    with db_factory.begin() as db:
        claim = claim_mail(db)
    with db_factory.begin() as db:
        db.get(MailOutbox, claim.identifier).lease_expires_at = now() - timedelta(seconds=1)
    before = len(RecordingSMTP.instances)
    assert deliver_claim(
        db_factory, app.state.settings, claim, smtp_factory=RecordingSMTP
    ) is False
    assert len(RecordingSMTP.instances) == before


def test_stale_mail_claim_cannot_cancel_or_overwrite_replacement_lease(
    client, account, app, db_factory
):
    enable_mail(app.state.settings)
    assert client.post("/api/v1/auth/verification/request", json={}).status_code == 202
    with db_factory.begin() as db:
        stale_claim = claim_mail(db)
    with db_factory.begin() as db:
        db.get(MailOutbox, stale_claim.identifier).lease_expires_at = now() - timedelta(seconds=1)
    with db_factory.begin() as db:
        replacement_claim = claim_mail(db)
    assert replacement_claim.identifier == stale_claim.identifier
    assert replacement_claim.lease_token != stale_claim.lease_token

    before = len(RecordingSMTP.instances)
    assert deliver_claim(
        db_factory,
        app.state.settings,
        stale_claim,
        smtp_factory=RecordingSMTP,
    ) is False
    assert len(RecordingSMTP.instances) == before
    with db_factory() as db:
        outbox = db.get(MailOutbox, replacement_claim.identifier)
        assert outbox.status == "sending"
        assert outbox.lease_token_hash == digest(replacement_claim.lease_token)
        assert outbox.body_ciphertext == replacement_claim.ciphertext

class StallingSMTP(RecordingSMTP):
    """Spend the whole conversation budget in the greeting, before any message is sent."""

    clock = None

    def ehlo(self):
        self.clock.advance(60)


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float):
        self.value += seconds


def test_mail_delivery_stops_at_the_whole_conversation_deadline(
    client, account, app, db_factory
):
    enable_mail(app.state.settings)
    app.state.settings.smtp_timeout_seconds = 15
    app.state.settings.smtp_conversation_seconds = 45
    assert client.post("/api/v1/auth/verification/request", json={}).status_code == 202
    with db_factory.begin() as db:
        claim = claim_mail(db)
    clock = FakeClock()
    StallingSMTP.clock = clock
    before = len(StallingSMTP.instances)
    assert deliver_claim(
        db_factory,
        app.state.settings,
        claim,
        smtp_factory=StallingSMTP,
        clock=clock,
    ) is False
    assert len(StallingSMTP.instances) == before + 1
    conversation = StallingSMTP.instances[-1]
    # A single socket operation stays bounded, and the message is never handed over.
    assert conversation.timeout == 15
    assert conversation.messages == []
    assert conversation.closed is True
    with db_factory() as db:
        outbox = db.get(MailOutbox, claim.identifier)
        assert outbox.status == "queued"
        assert outbox.last_error_code == "smtp_deadline"
        assert outbox.attempts == 1
        # A retryable stall keeps the ciphertext for the next bounded attempt.
        assert outbox.body_ciphertext != ""


def test_the_conversation_bound_limits_each_step_and_then_expires(settings):
    enable_mail(settings)
    settings.smtp_timeout_seconds = 15
    settings.smtp_conversation_seconds = 45
    clock = FakeClock()
    deadline = MailDeadline(settings, clock)
    assert deadline.remaining() == 15
    clock.advance(35)
    assert deadline.remaining() == 10
    clock.advance(10)
    with pytest.raises(MailDeadlineExceeded):
        deadline.remaining()


def test_mail_configuration_requires_a_conversation_bound_above_one_operation():
    key = base64.urlsafe_b64encode(b"k" * 32).decode()
    common = {
        "env": "test",
        "mail_enabled": True,
        "mail_encryption_key": key,
        "smtp_host": "mail.example.com",
        "smtp_from": "identity@lotuspond.example.com",
    }
    with pytest.raises(ValidationError):
        Settings(smtp_timeout_seconds=20, smtp_conversation_seconds=10, **common)
    accepted = Settings(smtp_timeout_seconds=20, smtp_conversation_seconds=20, **common)
    assert accepted.smtp_conversation_seconds == 20


def test_mail_configuration_rejects_unsafe_hosts_and_plaintext_transport():
    key = base64.urlsafe_b64encode(b"k" * 32).decode()
    common = {
        "env": "test",
        "mail_enabled": True,
        "mail_encryption_key": key,
        "smtp_from": "identity@lotuspond.example.com",
        "smtp_starttls": False,
    }
    for host in ("mail.example:25", "mail.example\ninvalid", "mail.example.com"):
        try:
            Settings(smtp_host=host, **common)
        except ValidationError as error:
            assert key not in str(error)
        else:
            raise AssertionError(f"unsafe SMTP host accepted: {host!r}")
    assert Settings(smtp_host="::1", **common).smtp_host == "::1"
    sentinel = "smtp-password-must-not-appear"
    try:
        Settings(
            smtp_host="mail.example.com",
            smtp_username="identity@example.com",
            smtp_password=sentinel,
            **common,
        )
    except ValidationError as error:
        assert sentinel not in str(error)
    else:
        raise AssertionError("unsafe plaintext SMTP configuration was accepted")


def test_mail_ciphertext_is_bound_to_its_outbox_identifier(settings):
    enable_mail(settings)
    ciphertext = encrypt_payload(
        settings,
        "outbox-one",
        {"to": "person@example.com", "subject": "Subject", "body": "Body"},
    )
    with pytest.raises(InvalidTag):
        decrypt_payload(settings, "outbox-two", ciphertext)


def test_production_organization_access_requires_verified_key_creator(
    client, account, app, db_factory
):
    key = client.post(
        "/api/v1/api-keys", json={"name": "Reader", "scopes": ["read"]}
    ).json()["key"]
    app.state.settings.env = "production"
    with TestClient(app, headers={"authorization": f"Bearer {key}"}) as service:
        response = service.get("/api/v1/projects")
        assert response.status_code == 403
        assert response.json()["error"] == "identity_unverified"
    with db_factory.begin() as db:
        user = db.scalar(select(User).where(User.email == "owner@example.com"))
        user.email_verified = True
    app.state.settings.env = "test"
