from __future__ import annotations

import os
import stat
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import select

from lotuspond.models import (
    ApiKey,
    AuditEvent,
    LoginSession,
    MfaEnrollment,
    MfaFactor,
    MfaLoginChallenge,
    MfaRecoveryCode,
    OfflineEnrollment,
    OfflineIdentityAttestation,
    Project,
    User,
    Workspace,
    now,
)
from lotuspond.offline_identity import (
    ProvisioningRequest,
    ReissueRequest,
    RevocationRequest,
    provision_identity,
    reissue_enrollment,
    revoke_identity,
)
from lotuspond.offline_provision import _write_token_file
from lotuspond.security import digest, verify_password


def provision(db_factory, **overrides):
    values = {
        "email": "offline@example.com",
        "name": "Offline Owner",
        "attestation_source": "in_person",
        "source_reference": "badge-42",
        "operator_reference": "operator-7",
        "organization_name": "Offline Organization",
        "project_names": ("Safety evaluation",),
        "platform_admin": True,
    }
    values.update(overrides)
    with db_factory() as db, db.begin():
        return provision_identity(db, ProvisioningRequest(**values))


def enable_airgap(app) -> None:
    app.state.settings.network_mode = "airgap"
    app.state.settings.mail_enabled = False


def test_provision_is_new_pending_hashed_and_audited(db_factory):
    result = provision(db_factory)
    with db_factory() as db:
        user = db.get(User, result.user_id)
        enrollment = db.get(OfflineEnrollment, result.enrollment_id)
        attestation = db.get(OfflineIdentityAttestation, enrollment.attestation_id)
        workspaces = list(
            db.scalars(select(Workspace).where(Workspace.owner_id == result.user_id))
        )
        projects = list(db.scalars(select(Project)))
        audit = db.scalar(
            select(AuditEvent).where(AuditEvent.action == "account.offline_provisioned")
        )
    assert user.email_verified is False
    assert user.is_platform_admin is True
    assert verify_password(result.token, user.password_hash) is False
    assert enrollment.token_hash == digest(result.token)
    assert result.token not in str(audit.details)
    assert audit.details["source_reference"] == "badge-42"
    assert {item.kind for item in workspaces} == {"personal", "organization"}
    assert [item.name for item in projects] == ["Safety evaluation"]
    assert attestation.source == "in_person"


def test_provision_refuses_existing_account_and_implicit_projects(db_factory):
    provision(db_factory)
    with db_factory() as db, db.begin():
        with pytest.raises(ValueError, match="already exists"):
            provision_identity(
                db,
                ProvisioningRequest(
                    email="offline@example.com",
                    name="Replacement",
                    attestation_source="in_person",
                    source_reference="ref",
                    operator_reference="operator",
                ),
            )
        with pytest.raises(ValueError, match="explicit organization"):
            provision_identity(
                db,
                ProvisioningRequest(
                    email="another@example.com",
                    name="Another",
                    attestation_source="in_person",
                    source_reference="ref",
                    operator_reference="operator",
                    project_names=("Implicit project",),
                ),
            )


def test_inspect_complete_requires_airgap_and_normal_login(
    app, client, db_factory
):
    result = provision(db_factory)
    unavailable = client.post(
        "/api/v1/auth/offline-enrollment/inspect", json={"token": result.token}
    )
    assert unavailable.status_code == 404
    enable_airgap(app)
    inspected = client.post(
        "/api/v1/auth/offline-enrollment/inspect", json={"token": result.token}
    )
    assert inspected.status_code == 200, inspected.text
    assert inspected.json() == {
        "email": "offline@example.com",
        "name": "Offline Owner",
        "expires_at": inspected.json()["expires_at"],
        "organization": {
            "id": result.selected_workspace_id,
            "name": "Offline Organization",
        },
        "projects": [{"id": inspected.json()["projects"][0]["id"], "name": "Safety evaluation"}],
        "identity_verification_source": "operator_attested",
    }
    assert "badge-42" not in inspected.text
    completed = client.post(
        "/api/v1/auth/offline-enrollment/complete",
        json={"token": result.token, "password": "new-offline-password"},
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["email_verified"] is False
    assert completed.json()["identity_verification_source"] == "operator_attested"
    assert client.get("/api/v1/me").status_code == 401
    replay = client.post(
        "/api/v1/auth/offline-enrollment/complete",
        json={"token": result.token, "password": "another-valid-password"},
    )
    assert replay.status_code == 409
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "offline@example.com", "password": "new-offline-password"},
    )
    assert login.status_code == 200
    me = client.get("/api/v1/me").json()["user"]
    assert me["email_verified"] is False
    assert me["identity_verified"] is True
    assert me["identity_verification_source"] == "operator_attested"


def test_enrollment_requires_explicit_same_origin(app, db_factory):
    from fastapi.testclient import TestClient

    enable_airgap(app)
    result = provision(db_factory)
    with TestClient(app) as no_origin:
        response = no_origin.post(
            "/api/v1/auth/offline-enrollment/inspect", json={"token": result.token}
        )
    assert response.status_code == 403
    assert response.json()["error"] == "invalid_origin"


def test_pending_session_and_key_are_blocked_and_stay_revoked_after_completion(
    app, client, db_factory
):
    result = provision(db_factory)
    raw_session = "pending-session"
    raw_key = "lp_pending-key"
    with db_factory() as db, db.begin():
        db.add(
            LoginSession(
                user_id=result.user_id,
                token_hash=digest(raw_session),
                expires_at=now() + timedelta(hours=1),
            )
        )
        db.add(
            ApiKey(
                workspace_id=result.selected_workspace_id,
                name="pending key",
                token_hash=digest(raw_key),
                prefix=raw_key[:11],
                scopes=["read"],
                expires_at=now() + timedelta(hours=1),
                created_by_user_id=result.user_id,
            )
        )
    client.cookies.set("lotuspond_session", raw_session)
    client.headers["x-lotuspond-workspace"] = result.selected_workspace_id
    assert client.get("/api/v1/me").status_code == 401
    response = client.get(
        "/api/v1/overview", headers={"authorization": f"Bearer {raw_key}"}
    )
    assert response.status_code == 401
    enable_airgap(app)
    completed = client.post(
        "/api/v1/auth/offline-enrollment/complete",
        json={"token": result.token, "password": "new-offline-password"},
    )
    assert completed.status_code == 200
    client.cookies.set("lotuspond_session", raw_session)
    assert client.get("/api/v1/me").status_code == 401
    response = client.get(
        "/api/v1/overview", headers={"authorization": f"Bearer {raw_key}"}
    )
    assert response.status_code == 401
    with db_factory() as db:
        assert db.scalar(
            select(LoginSession.id).where(LoginSession.user_id == result.user_id)
        ) is None
        key = db.scalar(select(ApiKey).where(ApiKey.token_hash == digest(raw_key)))
        assert key.revoked_at is not None


def test_expired_disabled_and_revoked_attestations_fail_identity_gate(
    app, client, db_factory
):
    enable_airgap(app)
    for state in ("expired", "disabled", "revoked"):
        result = provision(db_factory, email=f"{state}@example.com")
        response = client.post(
            "/api/v1/auth/offline-enrollment/complete",
            json={"token": result.token, "password": "new-offline-password"},
        )
        assert response.status_code == 200
        with db_factory() as db, db.begin():
            enrollment = db.get(OfflineEnrollment, result.enrollment_id)
            attestation = db.get(OfflineIdentityAttestation, enrollment.attestation_id)
            user = db.get(User, result.user_id)
            if state == "expired":
                attestation.attested_at = now() - timedelta(days=2)
                attestation.expires_at = now() - timedelta(days=1)
            elif state == "disabled":
                user.disabled_at = now()
            else:
                attestation.revoked_at = now()
        login = client.post(
            "/api/v1/auth/login",
            json={"email": f"{state}@example.com", "password": "new-offline-password"},
        )
        if state == "disabled":
            assert login.status_code == 401
            continue
        assert login.status_code == 200
        app.state.settings.env = "production"
        client.headers["x-lotuspond-workspace"] = result.selected_workspace_id
        assert client.get("/api/v1/overview").status_code == 403
        client.post("/api/v1/auth/logout", json={})
        app.state.settings.env = "test"


def test_expired_token_can_be_reissued_but_completed_account_cannot(db_factory):
    original = provision(db_factory)
    with db_factory() as db, db.begin():
        db.get(OfflineEnrollment, original.enrollment_id).expires_at = now() - timedelta(seconds=1)
        replacement = reissue_enrollment(
            db,
            ReissueRequest(
                user_id=original.user_id,
                email="offline@example.com",
                operator_reference="operator-8",
            ),
        )
    assert replacement.token != original.token
    with db_factory() as db, db.begin():
        assert db.get(OfflineEnrollment, original.enrollment_id).revoked_at is not None
        db.get(OfflineEnrollment, replacement.enrollment_id).consumed_at = now()
    with db_factory() as db, db.begin(), pytest.raises(ValueError, match="uncompleted"):
        reissue_enrollment(
            db,
            ReissueRequest(
                user_id=original.user_id,
                email="offline@example.com",
                operator_reference="operator-9",
            ),
        )


@pytest.mark.parametrize("state", ["disabled", "future_attestation"])
def test_reissue_refuses_inactive_identity(db_factory, state):
    original = provision(db_factory)
    with db_factory() as db, db.begin():
        enrollment = db.get(OfflineEnrollment, original.enrollment_id)
        attestation = db.get(OfflineIdentityAttestation, enrollment.attestation_id)
        user = db.get(User, original.user_id)
        if state == "disabled":
            user.disabled_at = now()
        else:
            attestation.attested_at = now() + timedelta(days=1)
            attestation.expires_at = now() + timedelta(days=2)
    expected = "Disabled" if state == "disabled" else "no longer active"
    with db_factory() as db, db.begin(), pytest.raises(ValueError, match=expected):
        reissue_enrollment(
            db,
            ReissueRequest(
                user_id=original.user_id,
                email="offline@example.com",
                operator_reference="operator-10",
            ),
        )


def test_token_file_is_exclusive_private_and_rejects_symlink(tmp_path: Path):
    token_file = tmp_path / "enrollment.txt"
    _write_token_file(token_file, "https://lotuspond.invalid/#enroll?token=secret")
    assert stat.S_IMODE(token_file.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        _write_token_file(token_file, "replacement")
    target = tmp_path / "target"
    target.write_text("preserve", encoding="utf-8")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(FileExistsError):
        _write_token_file(link, "replacement")
    assert target.read_text(encoding="utf-8") == "preserve"


def test_revoke_identity_disables_account_and_revokes_authority(db_factory):
    result = provision(db_factory)
    raw_session = "offline-session"
    raw_key = "lp_offline-key"
    with db_factory() as db, db.begin():
        db.add(
            LoginSession(
                user_id=result.user_id,
                token_hash=digest(raw_session),
                expires_at=now() + timedelta(hours=1),
            )
        )
        db.add(
            ApiKey(
                workspace_id=result.selected_workspace_id,
                name="offline key",
                token_hash=digest(raw_key),
                prefix=raw_key[:11],
                scopes=["read"],
                expires_at=now() + timedelta(hours=1),
                created_by_user_id=result.user_id,
            )
        )
        factor = MfaFactor(
            user_id=result.user_id,
            secret_ciphertext="test-ciphertext",
            version=1,
            enabled_at=now(),
        )
        db.add(factor)
        db.flush()
        db.add_all(
            [
                MfaEnrollment(
                    user_id=result.user_id,
                    secret_ciphertext="pending-ciphertext",
                    factor_version=1,
                    expires_at=now() + timedelta(minutes=10),
                ),
                MfaLoginChallenge(
                    user_id=result.user_id,
                    factor_id=factor.id,
                    factor_version=factor.version,
                    token_hash=digest("mfa-challenge"),
                    expires_at=now() + timedelta(minutes=5),
                ),
                MfaRecoveryCode(
                    user_id=result.user_id,
                    factor_id=factor.id,
                    factor_version=factor.version,
                    code_hash=digest("mfa-recovery"),
                ),
            ]
        )
    with db_factory() as db, db.begin():
        summary = revoke_identity(
            db,
            RevocationRequest(
                user_id=result.user_id,
                email="offline@example.com",
                operator_reference="operator-9",
                reason="credential reported compromised",
            ),
        )
    assert summary["revoked_attestations"] == 1
    assert summary["revoked_pending_enrollments"] == 1
    assert summary["revoked_sessions"] == 1
    assert summary["revoked_api_keys"] == 1
    with db_factory() as db:
        user = db.get(User, result.user_id)
        enrollment = db.get(OfflineEnrollment, result.enrollment_id)
        attestation = db.get(OfflineIdentityAttestation, enrollment.attestation_id)
        key = db.scalar(select(ApiKey).where(ApiKey.created_by_user_id == user.id))
        audit = db.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "account.offline_identity_revoked"
            )
        )
        assert user.disabled_at is not None
        assert enrollment.revoked_at is not None
        assert attestation.revoked_at is not None
        assert key.revoked_at is not None
        assert db.scalar(
            select(LoginSession.id).where(LoginSession.user_id == user.id)
        ) is None
        assert db.scalar(select(MfaFactor).where(MfaFactor.user_id == user.id)) is None
        assert db.scalar(
            select(MfaEnrollment).where(MfaEnrollment.user_id == user.id)
        ) is None
        assert db.scalar(
            select(MfaLoginChallenge).where(MfaLoginChallenge.user_id == user.id)
        ) is None
        assert db.scalar(
            select(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user.id)
        ) is None
        assert audit.details["reason"] == "credential reported compromised"


def test_revoke_identity_refuses_non_offline_account(client, db_factory):
    registered = client.post(
        "/api/v1/auth/register",
        json={
            "email": "mailbox@example.com",
            "name": "Mailbox Account",
            "password": "a-valid-test-password",
        },
    )
    assert registered.status_code == 201
    with db_factory() as db:
        user = db.scalar(select(User).where(User.email == "mailbox@example.com"))
        user_id = user.id
    with db_factory() as db, db.begin(), pytest.raises(
        ValueError, match="not provisioned"
    ):
        revoke_identity(
            db,
            RevocationRequest(
                user_id=user_id,
                email="mailbox@example.com",
                operator_reference="operator-9",
                reason="must not affect mailbox users",
            ),
        )
    with db_factory() as db:
        assert db.get(User, user_id).disabled_at is None


def test_concurrent_double_redemption_is_single_use(app, client, db_factory, engine):
    if engine.dialect.name != "postgresql" or not os.environ.get(
        "LOTUSPOND_PLATFORM_TEST_DATABASE_URL"
    ):
        pytest.skip("requires isolated PostgreSQL integration database")
    enable_airgap(app)
    result = provision(db_factory)

    def redeem():
        from fastapi.testclient import TestClient

        with TestClient(app, headers={"origin": "http://testserver"}) as concurrent:
            return concurrent.post(
                "/api/v1/auth/offline-enrollment/complete",
                json={"token": result.token, "password": "new-offline-password"},
            ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = sorted(pool.map(lambda _index: redeem(), range(2)))
    assert statuses == [200, 409]


def test_concurrent_reissue_and_completion_preserve_one_authority(
    app, db_factory, engine
):
    if engine.dialect.name != "postgresql" or not os.environ.get(
        "LOTUSPOND_PLATFORM_TEST_DATABASE_URL"
    ):
        pytest.skip("requires isolated PostgreSQL integration database")
    enable_airgap(app)
    original = provision(db_factory)
    barrier = Barrier(2)

    def complete():
        from fastapi.testclient import TestClient

        barrier.wait()
        with TestClient(app, headers={"origin": "http://testserver"}) as concurrent:
            return (
                "complete",
                concurrent.post(
                    "/api/v1/auth/offline-enrollment/complete",
                    json={
                        "token": original.token,
                        "password": "new-offline-password",
                    },
                ).status_code,
            )

    def reissue():
        barrier.wait()
        try:
            with db_factory() as db, db.begin():
                replacement = reissue_enrollment(
                    db,
                    ReissueRequest(
                        user_id=original.user_id,
                        email="offline@example.com",
                        operator_reference="operator-concurrent",
                    ),
                )
            return "reissue", replacement.enrollment_id
        except ValueError:
            return "reissue", None

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(operation) for operation in (complete, reissue)]
        outcomes = dict(future.result() for future in futures)
    assert (outcomes["complete"] == 200) != (outcomes["reissue"] is not None)
    with db_factory() as db:
        rows = list(
            db.scalars(
                select(OfflineEnrollment).where(
                    OfflineEnrollment.user_id == original.user_id
                )
            )
        )
    assert sum(item.consumed_at is not None for item in rows) + sum(
        item.consumed_at is None and item.revoked_at is None for item in rows
    ) == 1
    assert all(not (item.consumed_at and item.revoked_at) for item in rows)
