import base64
import re
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, local

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import event, func, select

from lotuspond import authority_gate, identity
from lotuspond.mail import decrypt_payload
from lotuspond.models import (
    IdentityChallenge,
    Invitation,
    LoginSession,
    MailOutbox,
    Membership,
    User,
)
from lotuspond.security import verify_password


def enable_test_mail(settings):
    settings.mail_enabled = True
    settings.mail_encryption_key = SecretStr(base64.urlsafe_b64encode(b"k" * 32).decode())
    settings.smtp_host = "localhost"
    settings.smtp_port = 2525
    settings.smtp_from = "identity@lotuspond.example.com"
    settings.smtp_starttls = False


def queued_token(db_factory, settings, kind):
    with db_factory() as db:
        outbox = db.scalar(
            select(MailOutbox)
            .where(MailOutbox.kind == kind)
            .order_by(MailOutbox.created_at.desc(), MailOutbox.id.desc())
        )
        body = decrypt_payload(settings, outbox.id, outbox.body_ciphertext)["body"]
    match = re.search(r"token=([A-Za-z0-9_-]+)", body)
    assert match
    return match.group(1)


def test_postgres_runner_claims_do_not_duplicate(app, engine, client, account):
    if engine.dialect.name != "postgresql":
        pytest.skip("Row-lock contention requires the isolated PostgreSQL test database")
    project = client.post("/api/v1/projects", json={"name": "Claim contention"}).json()
    assert (
        client.post(
            "/api/v1/scenarios",
            json={"project_id": project["id"], "name": "One scenario", "input": {}, "expected": {}},
        ).status_code
        == 201
    )
    run = client.post(
        "/api/v1/runs", json={"project_id": project["id"], "name": "One run", "revision": "1"}
    ).json()
    runners = [
        client.post(
            "/api/v1/runners", json={"name": f"Runner {i}", "capabilities": ["text"]}
        ).json()
        for i in range(4)
    ]
    key = client.post("/api/v1/api-keys", json={"name": "Workers", "scopes": ["run"]}).json()["key"]

    def claim(runner):
        with TestClient(app, headers={"authorization": f"Bearer {key}"}) as caller:
            response = caller.post(f"/api/v1/runners/{runner['id']}/claim", json={})
            assert response.status_code == 200
            return response.json()

    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(claim, runners))
    assert len([row for row in claims if row["run"]]) == 1
    assert next(row["run"]["id"] for row in claims if row["run"]) == run["id"]


def test_old_password_login_waits_for_password_change(app, engine, client, account, monkeypatch):
    if engine.dialect.name != "postgresql":
        pytest.skip("Password rotation contention requires isolated PostgreSQL")
    changing = Event()
    login_waiting = Event()
    request_thread = local()
    real_verify = identity.verify_password

    def verify(password, encoded):
        if getattr(request_thread, "changing", False):
            changing.set()
            assert login_waiting.wait(5), "The competing login did not reach its user gate"
        return real_verify(password, encoded)

    # FastAPI's sync route runs in an AnyIO worker, so label the requests at the
    # SQL boundary instead of relying on the caller thread's name. The login waits at
    # the user's fair gate, taken before its row lock (`authority_gate`).
    def observe(connection, cursor, statement, parameters, context, many):
        if "FROM users" in statement and "FOR UPDATE" in statement and not changing.is_set():
            request_thread.changing = True
        elif (
            changing.is_set()
            and "pg_advisory_xact_lock(" in statement
            and parameters.get("namespace") == authority_gate.USER
        ):
            request_thread.changing = False
            login_waiting.set()

    event.listen(engine, "before_cursor_execute", observe)
    monkeypatch.setattr(identity, "verify_password", verify)
    cookie = client.cookies.get("lotuspond_session")

    def change():
        with TestClient(app, headers={"origin": "http://testserver"}) as device:
            device.cookies.set("lotuspond_session", cookie)
            return device.post(
                "/api/v1/auth/password",
                json={
                    "current_password": "a-valid-test-password",
                    "new_password": "a-new-test-password",
                },
            ).status_code

    def login_with_old_password():
        assert changing.wait(5)
        with TestClient(app, headers={"origin": "http://testserver"}) as device:
            return device.post(
                "/api/v1/auth/login",
                json={"email": "owner@example.com", "password": "a-valid-test-password"},
            ).status_code

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending_change = pool.submit(change)
            pending_login = pool.submit(login_with_old_password)
            assert pending_change.result(timeout=10) == 200
            assert pending_login.result(timeout=10) == 401
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert client.get("/api/v1/me").status_code == 401


def test_postgres_invitation_acceptance_is_single_consumer(
    app, engine, client, account, db_factory
):
    if engine.dialect.name != "postgresql":
        pytest.skip("Invitation contention requires isolated PostgreSQL")
    enable_test_mail(app.state.settings)
    with TestClient(app, headers={"origin": "http://testserver"}) as invitee:
        assert (
            invitee.post(
                "/api/v1/auth/register",
                json={
                    "email": "concurrent-invitee@example.com",
                    "name": "Concurrent Invitee",
                    "password": "a-valid-test-password",
                },
            ).status_code
            == 201
        )
    with db_factory.begin() as db:
        user = db.scalar(select(User).where(User.email == "concurrent-invitee@example.com"))
        user.email_verified = True
    assert (
        client.post(
            "/api/v1/invitations",
            json={"email": "concurrent-invitee@example.com", "role": "editor"},
        ).status_code
        == 201
    )
    token = queued_token(db_factory, app.state.settings, "invitation")
    barrier = Barrier(2)

    def accept():
        with TestClient(app, headers={"origin": "http://testserver"}) as caller:
            assert (
                caller.post(
                    "/api/v1/auth/login",
                    json={
                        "email": "concurrent-invitee@example.com",
                        "password": "a-valid-test-password",
                    },
                ).status_code
                == 200
            )
            barrier.wait(timeout=5)
            response = caller.post("/api/v1/invitations/accept", json={"token": token})
            return response.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = [
            future.result(timeout=10) for future in [pool.submit(accept), pool.submit(accept)]
        ]
    assert sorted(statuses) == [200, 400]
    with db_factory() as db:
        user = db.scalar(select(User).where(User.email == "concurrent-invitee@example.com"))
        assert (
            db.scalar(
                select(func.count())
                .select_from(Membership)
                .where(Membership.workspace_id == account, Membership.user_id == user.id)
            )
            == 1
        )
        invitation = db.scalar(
            select(Invitation).where(Invitation.email == "concurrent-invitee@example.com")
        )
        assert invitation.accepted_at is not None


def test_postgres_password_reset_is_single_consumer(app, engine, client, account, db_factory):
    if engine.dialect.name != "postgresql":
        pytest.skip("Password-reset contention requires isolated PostgreSQL")
    enable_test_mail(app.state.settings)
    assert (
        client.post(
            "/api/v1/auth/password/reset/request", json={"email": "owner@example.com"}
        ).status_code
        == 202
    )
    token = queued_token(db_factory, app.state.settings, "password_reset")
    passwords = ("first-concurrent-password", "second-concurrent-password")
    barrier = Barrier(2)

    def confirm(password):
        with TestClient(app, headers={"origin": "http://testserver"}) as caller:
            barrier.wait(timeout=5)
            response = caller.post(
                "/api/v1/auth/password/reset/confirm",
                json={"token": token, "new_password": password},
            )
            return response.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = [
            future.result(timeout=10)
            for future in [pool.submit(confirm, password) for password in passwords]
        ]
    assert sorted(statuses) == [200, 400]
    with db_factory() as db:
        user = db.scalar(select(User).where(User.email == "owner@example.com"))
        assert sum(verify_password(password, user.password_hash) for password in passwords) == 1
        assert (
            db.scalar(
                select(func.count())
                .select_from(LoginSession)
                .where(LoginSession.user_id == user.id)
            )
            == 0
        )
        challenge = db.scalar(
            select(IdentityChallenge).where(IdentityChallenge.kind == "password_reset")
        )
        assert challenge.consumed_at is not None
