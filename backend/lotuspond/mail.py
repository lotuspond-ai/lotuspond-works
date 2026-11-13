import base64
import json
import secrets
import smtplib
import ssl
from dataclasses import dataclass
from datetime import timedelta
from email.message import EmailMessage
from time import monotonic

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .config import Settings
from .models import (
    AuditEvent,
    IdentityChallenge,
    Invitation,
    MailOutbox,
    User,
    Workspace,
    new_id,
    now,
)
from .security import digest, new_token, utc

MAIL_AAD_PREFIX = b"lotuspond-mail-outbox-v1:"
MAX_MAIL_PLAINTEXT_BYTES = 64 * 1024
MAX_ATTEMPTS = 5
CLOSING_SECONDS = 2
SECURITY_NOTICE_KIND = "security_notice"
SECURITY_NOTICE_SUBJECT = "Your LotusPond Works sign-in settings changed"
SECURITY_NOTICE_CHANGES = {
    "mfa_enabled": "An authenticator was added to your account.",
    "mfa_disabled": "The authenticator on your account was removed.",
    "mfa_recovery_regenerated": "New recovery codes were issued for your account.",
    "password_changed": "Your password was changed.",
    "password_reset": "Your password was reset using an emailed recovery link.",
    "mfa_admin_reset": (
        "An administrator of your organization cleared the authenticator on your account "
        "so you can enrol a new one."
    ),
}


class MailDeadlineExceeded(smtplib.SMTPException):
    """The whole SMTP conversation passed its configured bound."""


class MailDeadline:
    """Bound the entire SMTP conversation, not only each socket operation."""

    def __init__(self, settings: Settings, clock=monotonic):
        self.clock = clock
        self.expires_at = clock() + settings.smtp_conversation_seconds
        self.operation_seconds = settings.smtp_timeout_seconds

    def remaining(self) -> float:
        left = self.expires_at - self.clock()
        if left <= 0:
            raise MailDeadlineExceeded("The SMTP conversation passed its deadline")
        return min(left, self.operation_seconds)

    def apply(self, client):
        """Raise once the whole conversation is spent; otherwise bound the next step."""
        budget = self.remaining()
        socket = getattr(client, "sock", None)
        if socket is not None:
            socket.settimeout(budget)

    def release(self, client):
        """Bound the closing exchange without replacing an in-flight failure."""
        socket = getattr(client, "sock", None)
        if socket is None:
            return
        try:
            socket.settimeout(min(self.operation_seconds, CLOSING_SECONDS))
        except OSError:
            pass


@dataclass(frozen=True)
class MailClaim:
    identifier: str
    lease_token: str
    ciphertext: str


def _key(settings: Settings) -> bytes:
    if not settings.mail_encryption_key:
        raise ValueError("Mail encryption is not configured")
    return base64.b64decode(
        settings.mail_encryption_key.get_secret_value(), altchars=b"-_", validate=True
    )


def encrypt_payload(settings: Settings, identifier: str, payload: dict[str, str]) -> str:
    plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    if len(plaintext) > MAX_MAIL_PLAINTEXT_BYTES:
        raise ValueError("Mail payload exceeds the configured bound")
    nonce = secrets.token_bytes(12)
    encrypted = AESGCM(_key(settings)).encrypt(
        nonce, plaintext, MAIL_AAD_PREFIX + identifier.encode("ascii")
    )
    return "v1." + base64.urlsafe_b64encode(nonce + encrypted).decode("ascii")


def decrypt_payload(settings: Settings, identifier: str, ciphertext: str) -> dict[str, str]:
    if not ciphertext.startswith("v1.") or len(ciphertext) > 128 * 1024:
        raise ValueError("Unsupported mail ciphertext")
    packed = base64.b64decode(ciphertext[3:], altchars=b"-_", validate=True)
    if len(packed) < 29:
        raise ValueError("Invalid mail ciphertext")
    plaintext = AESGCM(_key(settings)).decrypt(
        packed[:12], packed[12:], MAIL_AAD_PREFIX + identifier.encode("ascii")
    )
    if len(plaintext) > MAX_MAIL_PLAINTEXT_BYTES:
        raise ValueError("Mail plaintext exceeds the configured bound")
    value = json.loads(plaintext)
    if set(value) != {"to", "subject", "body"} or not all(
        isinstance(item, str) for item in value.values()
    ):
        raise ValueError("Invalid mail payload")
    if len(value["to"]) > 254 or len(value["subject"]) > 200 or len(value["body"]) > 32_000:
        raise ValueError("Invalid mail payload")
    return value


def enqueue_mail(
    db: Session,
    settings: Settings,
    *,
    kind: str,
    recipient: str,
    subject: str,
    body: str,
    actor_id: str,
    workspace_id: str | None = None,
    challenge_id: str | None = None,
    invitation_id: str | None = None,
) -> MailOutbox:
    if not settings.mail_enabled:
        raise ValueError("Mail delivery is disabled")
    outbox = MailOutbox(
        id=new_id(),
        workspace_id=workspace_id,
        identity_challenge_id=challenge_id,
        invitation_id=invitation_id,
        kind=kind,
        recipient_hash=digest(recipient),
        body_ciphertext="",
        status="queued",
        attempts=0,
        available_at=now(),
    )
    outbox.body_ciphertext = encrypt_payload(
        settings,
        outbox.id,
        {"to": recipient, "subject": subject, "body": body},
    )
    db.add(outbox)
    db.add(
        AuditEvent(
            workspace_id=workspace_id,
            actor_id=actor_id,
            action="mail.outbox_status",
            resource_id=outbox.id,
            details={"kind": kind, "status": "queued", "attempts": 0},
        )
    )
    return outbox


def security_notice_body(change: str, occurred_at) -> str:
    """Metadata only: no link, no token, no code, and nothing about other accounts."""
    return (
        f"{SECURITY_NOTICE_CHANGES[change]}\n\n"
        f"Recorded at {utc(occurred_at).strftime('%Y-%m-%d %H:%M:%S')} UTC. "
        "Your other sessions were signed out.\n\n"
        "If you did not make this change, reset your password and tell your LotusPond Works operator. "
        "This notification contains no links and no codes; LotusPond Works will never ask you to send "
        "a code or password in a reply."
    )


def enqueue_security_notice(
    db: Session,
    settings: Settings,
    *,
    user: User,
    change: str,
    occurred_at,
    workspace_id: str | None = None,
) -> MailOutbox | None:
    """Tell the account holder that its authentication material changed.

    A disabled or unconfigured mailbox never blocks the security change itself.
    """
    if change not in SECURITY_NOTICE_CHANGES:
        raise ValueError("Unsupported security notice")
    if not settings.mail_enabled or not settings.mail_encryption_key:
        return None
    return enqueue_mail(
        db,
        settings,
        kind=SECURITY_NOTICE_KIND,
        recipient=user.email,
        subject=SECURITY_NOTICE_SUBJECT,
        body=security_notice_body(change, occurred_at),
        actor_id=user.id,
        workspace_id=workspace_id,
    )


def _active_reference(db: Session, outbox: MailOutbox) -> bool:
    current = now()
    if outbox.kind == SECURITY_NOTICE_KIND:
        # It warns about a change that already happened, so nothing can make it stale.
        return True
    if outbox.identity_challenge_id:
        challenge = db.get(IdentityChallenge, outbox.identity_challenge_id)
        return bool(
            challenge
            and challenge.consumed_at is None
            and challenge.revoked_at is None
            and utc(challenge.expires_at) > current
        )
    invitation = db.get(Invitation, outbox.invitation_id)
    if not invitation:
        return False
    workspace = db.get(Workspace, invitation.workspace_id)
    return bool(
        invitation.accepted_at is None
        and invitation.revoked_at is None
        and utc(invitation.expires_at) > current
        and workspace
        and workspace.kind == "organization"
        and workspace.status == "active"
    )


def _audit_worker(db: Session, outbox: MailOutbox, status: str, error_code: str | None = None):
    details = {"kind": outbox.kind, "status": status, "attempts": outbox.attempts}
    if error_code:
        details["error_code"] = error_code
    db.add(
        AuditEvent(
            workspace_id=outbox.workspace_id,
            actor_id="mail-worker",
            action="mail.outbox_status",
            resource_id=outbox.id,
            details=details,
        )
    )


def claim_mail(db: Session) -> MailClaim | None:
    current = now()
    for _ in range(20):
        exhausted = db.scalar(
            select(MailOutbox)
            .where(
                MailOutbox.status == "sending",
                MailOutbox.attempts >= MAX_ATTEMPTS,
                MailOutbox.lease_expires_at < current,
            )
            .order_by(MailOutbox.created_at, MailOutbox.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if exhausted:
            exhausted.status = "failed"
            exhausted.body_ciphertext = ""
            exhausted.lease_expires_at = None
            exhausted.lease_token_hash = None
            exhausted.last_error_code = "attempts_exhausted"
            _audit_worker(db, exhausted, "failed", "attempts_exhausted")
            db.flush()
            continue
        query = (
            select(MailOutbox)
            .where(
                MailOutbox.attempts < MAX_ATTEMPTS,
                or_(
                    (MailOutbox.status == "queued") & (MailOutbox.available_at <= current),
                    (MailOutbox.status == "sending")
                    & (MailOutbox.lease_expires_at < current),
                ),
            )
            .order_by(MailOutbox.created_at, MailOutbox.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        outbox = db.scalar(query)
        if outbox is None:
            return None
        if not _active_reference(db, outbox):
            outbox.status = "cancelled"
            outbox.body_ciphertext = ""
            outbox.lease_expires_at = None
            outbox.lease_token_hash = None
            outbox.last_error_code = "reference_inactive"
            _audit_worker(db, outbox, "cancelled", "reference_inactive")
            db.flush()
            continue
        lease_token = new_token()
        outbox.status = "sending"
        outbox.attempts += 1
        outbox.lease_token_hash = digest(lease_token)
        outbox.lease_expires_at = current + timedelta(minutes=5)
        _audit_worker(db, outbox, "sending")
        db.flush()
        return MailClaim(outbox.id, lease_token, outbox.body_ciphertext)
    return None


def _smtp_send(
    settings: Settings, payload: dict[str, str], smtp_factory=smtplib.SMTP, clock=monotonic
):
    message = EmailMessage()
    message["From"] = str(settings.smtp_from)
    message["To"] = payload["to"]
    message["Subject"] = payload["subject"]
    message.set_content(payload["body"])
    deadline = MailDeadline(settings, clock)
    client = smtp_factory(settings.smtp_host, settings.smtp_port, timeout=deadline.remaining())
    try:
        deadline.apply(client)
        client.ehlo()
        if settings.smtp_starttls:
            deadline.apply(client)
            client.starttls(context=ssl.create_default_context())
            deadline.apply(client)
            client.ehlo()
        if settings.smtp_username and settings.smtp_password:
            deadline.apply(client)
            client.login(settings.smtp_username, settings.smtp_password.get_secret_value())
        deadline.apply(client)
        client.send_message(message)
    finally:
        deadline.release(client)
        try:
            client.quit()
        except (OSError, smtplib.SMTPException):
            try:
                client.close()
            except (OSError, smtplib.SMTPException):
                pass


def _complete(db: Session, claim: MailClaim) -> bool:
    current = now()
    outbox = db.scalar(
        select(MailOutbox)
        .where(
            MailOutbox.id == claim.identifier,
            MailOutbox.status == "sending",
            MailOutbox.lease_token_hash == digest(claim.lease_token),
            MailOutbox.lease_expires_at >= current,
        )
        .with_for_update()
    )
    if outbox:
        outbox.status = "sent"
        outbox.sent_at = now()
        outbox.body_ciphertext = ""
        outbox.lease_expires_at = None
        outbox.lease_token_hash = None
        outbox.last_error_code = None
        _audit_worker(db, outbox, "sent")
        return True
    return False


def _fail(db: Session, claim: MailClaim, error_code: str, *, terminal: bool = False):
    current = now()
    outbox = db.scalar(
        select(MailOutbox)
        .where(
            MailOutbox.id == claim.identifier,
            MailOutbox.status == "sending",
            MailOutbox.lease_token_hash == digest(claim.lease_token),
            MailOutbox.lease_expires_at >= current,
        )
        .with_for_update()
    )
    if not outbox:
        return
    terminal = terminal or outbox.attempts >= MAX_ATTEMPTS
    outbox.status = "failed" if terminal else "queued"
    outbox.available_at = now() + timedelta(seconds=min(900, 2**outbox.attempts * 15))
    outbox.lease_expires_at = None
    outbox.lease_token_hash = None
    outbox.last_error_code = error_code
    if terminal:
        outbox.body_ciphertext = ""
    _audit_worker(db, outbox, outbox.status, error_code)


def deliver_claim(
    factory,
    settings: Settings,
    claim: MailClaim,
    *,
    smtp_factory=smtplib.SMTP,
    clock=monotonic,
) -> bool:
    with factory.begin() as db:
        outbox = db.scalar(
            select(MailOutbox)
            .where(MailOutbox.id == claim.identifier)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            not outbox
            or outbox.status != "sending"
            or outbox.lease_token_hash != digest(claim.lease_token)
            or not outbox.lease_expires_at
            or utc(outbox.lease_expires_at) < now()
        ):
            return False
        if not _active_reference(db, outbox):
            outbox.status = "cancelled"
            outbox.body_ciphertext = ""
            outbox.lease_expires_at = None
            outbox.lease_token_hash = None
            outbox.last_error_code = "reference_inactive"
            _audit_worker(db, outbox, "cancelled", "reference_inactive")
            return False
    try:
        payload = decrypt_payload(settings, claim.identifier, claim.ciphertext)
        _smtp_send(settings, payload, smtp_factory, clock)
    except (InvalidTag, ValueError, json.JSONDecodeError):
        with factory.begin() as db:
            _fail(db, claim, "ciphertext_invalid", terminal=True)
        return False
    except MailDeadlineExceeded:
        with factory.begin() as db:
            _fail(db, claim, "smtp_deadline")
        return False
    except smtplib.SMTPException:
        with factory.begin() as db:
            _fail(db, claim, "smtp_error")
        return False
    except OSError:
        with factory.begin() as db:
            _fail(db, claim, "connection_error")
        return False
    with factory.begin() as db:
        return _complete(db, claim)


def run_once(factory, settings: Settings, *, smtp_factory=smtplib.SMTP) -> bool | None:
    if not settings.mail_enabled:
        return None
    with factory.begin() as db:
        claim = claim_mail(db)
    if claim is None:
        return None
    return deliver_claim(factory, settings, claim, smtp_factory=smtp_factory)
