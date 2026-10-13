import base64
import re

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from lotuspond.mail import (
    SECURITY_NOTICE_KIND,
    claim_mail,
    decrypt_payload,
    deliver_claim,
    enqueue_security_notice,
)
from lotuspond.mfa import _totp_at
from lotuspond.models import MailOutbox, User, now

PASSWORD = "a-valid-test-password"


def enable_mail(settings):
    settings.mail_enabled = True
    settings.mail_encryption_key = SecretStr(base64.urlsafe_b64encode(b"k" * 32).decode())
    settings.smtp_host = "localhost"
    settings.smtp_port = 2525
    settings.smtp_from = "identity@lotuspond.example.com"
    settings.smtp_starttls = False


def enable_mfa(app):
    app.state.settings.mfa_encryption_key = SecretStr(base64.urlsafe_b64encode(b"m" * 32).decode())


def code_for(secret: str) -> str:
    return _totp_at(base64.b32decode(secret + "=" * (-len(secret) % 8)), int(now().timestamp()))


def enroll(client, app) -> list[str]:
    enable_mfa(app)
    started = client.post(
        "/api/v1/auth/mfa/enrollment/start", json={"current_password": PASSWORD}
    )
    assert started.status_code == 200, started.text
    confirmed = client.post(
        "/api/v1/auth/mfa/enrollment/confirm", json={"code": code_for(started.json()["secret"])}
    )
    assert confirmed.status_code == 200, confirmed.text
    return confirmed.json()["recovery_codes"]


def notices(db_factory, settings) -> list[str]:
    with db_factory() as db:
        rows = list(
            db.scalars(
                select(MailOutbox)
                .where(MailOutbox.kind == SECURITY_NOTICE_KIND)
                .order_by(MailOutbox.created_at, MailOutbox.id)
            )
        )
        return [decrypt_payload(settings, row.id, row.body_ciphertext)["body"] for row in rows]


class RecordingSMTP:
    instances = []

    def __init__(self, host, port, timeout):
        self.messages = []
        self.closed = False
        self.__class__.instances.append(self)

    def ehlo(self):
        return None

    def starttls(self, context):
        return None

    def login(self, username, password):
        return None

    def send_message(self, message):
        self.messages.append(message)

    def quit(self):
        self.closed = True

    def close(self):
        self.closed = True


def test_every_factor_and_password_change_notifies_the_account(client, account, app, db_factory):
    enable_mail(app.state.settings)
    codes = enroll(client, app)
    # Each management step consumes a single-use recovery code rather than replaying a TOTP counter.
    regenerated = client.post(
        "/api/v1/auth/mfa/recovery-codes/regenerate",
        json={"current_password": PASSWORD, "code": codes[0]},
    )
    assert regenerated.status_code == 200, regenerated.text
    assert client.post(
        "/api/v1/auth/mfa/disable",
        json={"current_password": PASSWORD, "code": regenerated.json()["recovery_codes"][0]},
    ).status_code == 200
    assert client.post(
        "/api/v1/auth/password",
        json={"current_password": PASSWORD, "new_password": "another-valid-password"},
    ).status_code == 200
    queued = notices(db_factory, app.state.settings)
    assert [body.splitlines()[0] for body in queued] == [
        "An authenticator was added to your account.",
        "New recovery codes were issued for your account.",
        "The authenticator on your account was removed.",
        "Your password was changed.",
    ]
    assert all("Your other sessions were signed out." in body for body in queued)
    assert all("lpr_" not in body for body in queued)


def test_a_password_change_notifies_without_a_link_token_or_code(
    client, account, app, db_factory
):
    enable_mail(app.state.settings)
    assert client.post(
        "/api/v1/auth/password",
        json={"current_password": PASSWORD, "new_password": "another-valid-password"},
    ).status_code == 200
    queued = notices(db_factory, app.state.settings)
    assert len(queued) == 1
    body = queued[0]
    assert "Your password was changed." in body
    assert "no links and no codes" in body
    for forbidden in ("http://", "https://", "token=", "lpr_", "lpm_", PASSWORD):
        assert forbidden not in body
    assert not re.search(r"\b\d{6}\b", body)
    with db_factory() as db:
        row = db.scalar(select(MailOutbox).where(MailOutbox.kind == SECURITY_NOTICE_KIND))
        assert row.identity_challenge_id is None
        assert row.invitation_id is None
        assert row.workspace_id is not None


def test_a_password_reset_completion_notifies_the_account(client, account, app, db_factory):
    enable_mail(app.state.settings)
    assert client.post(
        "/api/v1/auth/password/reset/request", json={"email": "owner@example.com"}
    ).status_code == 202
    with db_factory() as db:
        row = db.scalar(
            select(MailOutbox)
            .where(MailOutbox.kind == "password_reset")
            .order_by(MailOutbox.created_at.desc(), MailOutbox.id.desc())
        )
        body = decrypt_payload(app.state.settings, row.id, row.body_ciphertext)["body"]
    token = re.search(r"token=([A-Za-z0-9_-]+)", body).group(1)
    assert client.post(
        "/api/v1/auth/password/reset/confirm",
        json={"token": token, "new_password": "a-reset-valid-password"},
    ).status_code == 200
    queued = notices(db_factory, app.state.settings)
    assert len(queued) == 1
    assert "Your password was reset using an emailed recovery link." in queued[0]


def test_a_disabled_mailbox_never_blocks_a_security_change(client, account, app, db_factory):
    app.state.settings.mail_enabled = False
    enroll(client, app)
    assert client.post(
        "/api/v1/auth/password",
        json={"current_password": PASSWORD, "new_password": "another-valid-password"},
    ).status_code == 200
    with db_factory() as db:
        assert db.scalars(select(MailOutbox)).all() == []


def test_the_worker_delivers_a_notice_that_references_nothing(client, account, app, db_factory):
    enable_mail(app.state.settings)
    assert client.post(
        "/api/v1/auth/password",
        json={"current_password": PASSWORD, "new_password": "another-valid-password"},
    ).status_code == 200
    with db_factory.begin() as db:
        claim = claim_mail(db)
    assert claim
    assert deliver_claim(
        db_factory, app.state.settings, claim, smtp_factory=RecordingSMTP
    ) is True
    with db_factory() as db:
        row = db.get(MailOutbox, claim.identifier)
        assert row.kind == SECURITY_NOTICE_KIND
        assert row.status == "sent"
        assert row.body_ciphertext == ""
        assert row.sent_at is not None
    assert len(RecordingSMTP.instances[-1].messages) == 1


def test_the_database_keeps_notices_reference_free_and_other_kinds_referenced(
    client, account, app, db_factory
):
    enable_mail(app.state.settings)
    assert client.post("/api/v1/auth/verification/request", json={}).status_code == 202
    with db_factory() as db:
        challenge_row = db.scalar(
            select(MailOutbox).where(MailOutbox.kind == "email_verification")
        )
        challenge_id = challenge_row.identity_challenge_id
    with db_factory() as db:
        # A referenced row cannot be relabelled as a notice.
        with pytest.raises(IntegrityError):
            db.execute(
                MailOutbox.__table__.update()
                .where(MailOutbox.id == challenge_row.id)
                .values(kind=SECURITY_NOTICE_KIND)
            )
        db.rollback()
    with db_factory() as db:
        # A notice cannot borrow another record's expiry as its own liveness.
        with pytest.raises(IntegrityError):
            db.execute(
                MailOutbox.__table__.insert().values(
                    id="00000000-0000-4000-8000-00000000f00d",
                    kind=SECURITY_NOTICE_KIND,
                    recipient_hash="a" * 64,
                    body_ciphertext="fixture",
                    status="queued",
                    attempts=0,
                    available_at=now(),
                    created_at=now(),
                    identity_challenge_id=challenge_id,
                )
            )
        db.rollback()
    with db_factory() as db:
        # And an ordinary kind still requires exactly one reference.
        with pytest.raises(IntegrityError):
            db.execute(
                MailOutbox.__table__.insert().values(
                    id="00000000-0000-4000-8000-00000000beef",
                    kind="email_verification",
                    recipient_hash="a" * 64,
                    body_ciphertext="fixture",
                    status="queued",
                    attempts=0,
                    available_at=now(),
                    created_at=now(),
                )
            )
        db.rollback()


def test_an_unsupported_change_name_is_refused_before_any_row_is_written(
    settings, db_factory, client, account
):
    enable_mail(settings)
    with db_factory() as db:
        user = db.scalar(select(User).where(User.email == "owner@example.com"))
        with pytest.raises(ValueError, match="Unsupported security notice"):
            enqueue_security_notice(
                db, settings, user=user, change="account_deleted", occurred_at=now()
            )
        assert db.scalars(select(MailOutbox)).all() == []
