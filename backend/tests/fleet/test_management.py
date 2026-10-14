"""Session-only scoped immutable administration and bounded private inventory."""

import copy
import json
from uuid import uuid4

import pytest
from sqlalchemy import select

from lotuspond import models

PIN = {"name": "lotuspond-fixture", "version": "1.0.0", "artifact_sha256": "a" * 64}
CONFIG = {
    "connector": PIN,
    "operations": ["inspect"],
    "destinations": [],
    "secrets": [],
    "data_handling": {
        "retain_payloads": False,
        "max_request_bytes": 65536,
        "max_response_bytes": 1048576,
        "timeout_ms": 10000,
    },
}
POLICY = {
    "connectors": [PIN],
    "platforms": ["linux"],
    "connection_ids": [],
    "max_instances": 1,
    "max_concurrent_invocations": 1,
}


@pytest.fixture
def fleet(client, account, app, db_factory):
    # Set before making fleet calls; absent routes are the intentional red boundary.
    object.__setattr__(app.state.settings, "fleet_enabled", True)
    object.__setattr__(app.state.settings, "fleet_installation_id", "test-installation")
    project, environment = str(uuid4()), str(uuid4())
    with db_factory.begin() as db:
        db.add(models.Project(id=project, workspace_id=account, name="Fleet"))
        db.flush()
        db.add(
            models.Environment(
                id=environment, workspace_id=account, project_id=project, name="Local", kind="local"
            )
        )
    return f"/api/v1/projects/{project}/environments/{environment}"


@pytest.fixture
def catalog(app):
    from lotuspond_runtime.protocol import ConnectorPin

    from lotuspond.fleet.catalog import InstalledCatalog

    app.state.fleet_catalog = InstalledCatalog.fixture(
        ConnectorPin(**PIN), secret_bindings=frozenset({("fixture-binding", 1)}), test_mode=True
    )


def post(client, path, body, key="key"):
    return client.post(path, json=body, headers={"Idempotency-Key": key})


def create(client, fleet, collection="connections", key="create"):
    result = post(client, f"{fleet}/{collection}", {"name": "Test"}, key)
    assert result.status_code == 201, result.text
    return result.json()


@pytest.mark.parametrize(
    "role,expected", [("owner", 201), ("admin", 201), ("editor", 403), ("viewer", 403)]
)
def test_management_principal_matrix(client, fleet, db_factory, account, role, expected):
    with db_factory.begin() as db:
        db.scalar(
            select(models.Membership).where(models.Membership.workspace_id == account)
        ).role = role
    result = post(client, f"{fleet}/connections", {"name": "A"})
    assert result.status_code == expected
    assert client.get(f"{fleet}/connections").status_code == (200 if expected == 201 else 403)


@pytest.mark.parametrize("token", ["lp_bad", "lpr_bad", "lpe_bad", "lpp_bad", "x,y"])
def test_machine_tokens_cannot_administer(client, fleet, token):
    assert (
        client.get(f"{fleet}/connections", headers={"Authorization": f"Bearer {token}"}).status_code
        == 401
    )


def test_api_key_and_platform_operator_no_bypass(client, fleet, account, app, db_factory):
    from datetime import timedelta

    from lotuspond.security import digest

    with db_factory.begin() as db:
        user = db.scalar(select(models.User))
        user.is_platform_admin = True
        db.add(
            models.ApiKey(
                workspace_id=account,
                name="Synthetic",
                prefix="lp_test",
                token_hash=digest("lp_test"),
                scopes=["read", "write"],
                expires_at=models.now() + timedelta(hours=1),
            )
        )
    assert (
        client.get(f"{fleet}/connections", headers={"Authorization": "Bearer lp_test"}).status_code
        == 403
    )
    assert client.get(f"{fleet}/connections").status_code == 200
    with db_factory.begin() as db:
        db.delete(
            db.scalar(select(models.Membership).where(models.Membership.workspace_id == account))
        )
    assert post(client, f"{fleet}/connections", {"name": "A"}).status_code == 403


@pytest.mark.parametrize("failure", ["suspended", "mfa", "verification"])
def test_current_session_eligibility(client, fleet, account, app, db_factory, failure):
    with db_factory.begin() as db:
        if failure == "suspended":
            db.get(models.Workspace, account).status = "suspended"
        elif failure == "mfa":
            object.__setattr__(app.state.settings, "require_mfa", True)
            from pydantic import SecretStr

            object.__setattr__(app.state.settings, "mfa_encryption_key", SecretStr("synthetic"))
        else:
            object.__setattr__(app.state.settings, "env", "production")
    assert client.get(f"{fleet}/connections").status_code == 403


def test_nested_scope_not_found(client, fleet, account, db_factory):
    item = create(client, fleet)
    project, environment = str(uuid4()), str(uuid4())
    with db_factory.begin() as db:
        db.add(models.Project(id=project, workspace_id=account, name="Other"))
        db.flush()
        db.add(
            models.Environment(
                id=environment, workspace_id=account, project_id=project, name="Other", kind="local"
            )
        )
    wrong = [
        fleet.replace(fleet.split("/")[4], project),
        fleet.replace(fleet.split("/")[6], environment),
        f"/api/v1/projects/{project}/environments/{environment}",
    ]
    for prefix in wrong:
        assert client.get(f"{prefix}/connections/{item['id']}").status_code == 404
    with db_factory.begin() as db:
        other = models.Workspace(
            id=str(uuid4()),
            name="Foreign",
            slug="foreign",
            owner_id=db.get(models.Workspace, account).owner_id,
        )
        db.add(other)
        db.flush()
        db.add(models.Project(id=str(uuid4()), workspace_id=other.id, name="Foreign"))
    assert client.get(f"{fleet}/connections/{uuid4()}").status_code == 404


def test_immutable_versions_request_keys_and_terminal_status(
    client, fleet, catalog, db_factory, account
):
    item = create(client, fleet)
    same = post(client, f"{fleet}/connections", {"name": "Test"}, "create")
    assert same.json() == item
    assert post(client, f"{fleet}/connections", {"name": "Changed"}, "create").status_code == 409
    path = f"{fleet}/connections/{item['id']}"
    first = post(client, path + "/versions", CONFIG, "v1")
    assert first.status_code == 201, first.text
    replay = post(client, path + "/versions", CONFIG, "v1")
    assert replay.json() == first.json()
    detail = client.get(path + "/versions/1").json()
    assert detail["sha256"] == first.json()["sha256"]
    assert detail["config"]["connector"] == PIN
    changed = copy.deepcopy(CONFIG)
    changed["data_handling"]["timeout_ms"] = 100
    assert post(client, path + "/versions", changed, "v2").json()["version"] == 2
    assert client.get(path + "/versions/1").json() == detail
    assert client.get(path).json()["current_version"] == 2
    assert post(client, path + "/status", {"status": "disabled"}, "disable").status_code == 200
    assert post(client, path + "/versions", CONFIG, "v3").status_code == 409
    assert post(client, path + "/status", {"status": "revoked"}, "revoke").status_code == 200
    assert post(client, path + "/status", {"status": "active"}, "reactivate").status_code == 409
    with db_factory.begin() as db:
        db.scalar(
            select(models.Membership).where(models.Membership.workspace_id == account)
        ).role = "viewer"
    assert post(client, path + "/versions", CONFIG, "v1").status_code == 403


@pytest.mark.parametrize(
    "change", ["operation", "artifact", "effects", "capacity", "raw-path", "url"]
)
def test_catalog_cannot_be_widened(client, fleet, catalog, change):
    item = create(client, fleet)
    config = copy.deepcopy(CONFIG)
    if change == "operation":
        config["operations"] = ["write"]
    elif change == "artifact":
        config["connector"]["artifact_sha256"] = "b" * 64
    elif change == "effects":
        config["effect"] = "none"
    elif change == "capacity":
        pool = create(client, fleet, "runtime-pools", "pool")
        result = post(
            client, f"{fleet}/runtime-pools/{pool['id']}/policies", dict(POLICY, max_instances=1001)
        )
        assert result.status_code == 400
        return
    else:
        value = "/mounted/sentinel" if change == "raw-path" else "https://secret.invalid"
        assert (
            post(
                client, f"{fleet}/secret-references", {"binding_id": value, "version": 1}
            ).status_code
            == 400
        )
        return
    assert post(client, f"{fleet}/connections/{item['id']}/versions", config).status_code == 400


def test_secret_versions_scope_revocation_and_pool_publication(
    client, fleet, catalog, db_factory, account
):
    secret = post(
        client,
        f"{fleet}/secret-references",
        {"binding_id": "fixture-binding", "version": 1},
        "secret",
    )
    assert secret.status_code == 201, secret.text
    ref = secret.json()
    item = create(client, fleet)
    config = dict(CONFIG, secrets=[{"reference_id": ref["id"], "version": 1}])
    assert (
        post(client, f"{fleet}/connections/{item['id']}/versions", config, "version").status_code
        == 201
    )
    wrong = dict(config, secrets=[{"reference_id": ref["id"], "version": 2}])
    assert (
        post(client, f"{fleet}/connections/{item['id']}/versions", wrong, "wrong").status_code
        == 404
    )
    pool = create(client, fleet, "runtime-pools", "pool")
    path = f"{fleet}/runtime-pools/{pool['id']}"
    policy = dict(POLICY, connection_ids=[item["id"]])
    first = post(client, path + "/policies", policy, "policy")
    assert first.status_code == 201, first.text
    assert client.get(path).json()["current_version"] == 1
    assert client.get(path + "/policies").json()["items"] == [first.json()]
    assert (
        post(client, f"{fleet}/secret-references/{ref['id']}/revoke", {}, "revoke").status_code
        == 200
    )
    assert (
        post(client, f"{fleet}/connections/{item['id']}/versions", config, "revoked").status_code
        == 409
    )
    assert post(client, path + "/policies", policy, "revoked-policy").status_code == 409
    assert (
        post(
            client,
            f"{fleet}/secret-references",
            {"binding_id": "fixture-binding", "version": 1},
            "again",
        ).status_code
        == 409
    )


def test_inventory_and_audit_safe(client, fleet, catalog, db_factory):
    sentinel = "private.destination.invalid"
    item = create(client, fleet)
    config = dict(CONFIG, destinations=[{"host": sentinel, "port": 443, "path_prefix": "/private"}])
    assert post(client, f"{fleet}/connections/{item['id']}/versions", config).status_code == 201
    for n in range(3):
        create(client, fleet, key=f"create-{n}")
    first = client.get(f"{fleet}/connections?limit=2")
    assert first.headers["cache-control"] == "no-store"
    page = first.json()
    assert len(page["items"]) == 2 and page["next_after_id"] == page["items"][-1]["id"]
    second = client.get(f"{fleet}/connections?limit=2&after_id={page['next_after_id']}").json()
    assert len(second["items"]) == 2 and second["next_after_id"] is None
    assert set(i["id"] for i in page["items"]).isdisjoint(i["id"] for i in second["items"])
    assert sentinel not in first.text
    with db_factory() as db:
        audits = list(
            db.scalars(select(models.AuditEvent).where(models.AuditEvent.action.like("fleet.%")))
        )
        assert len(audits) == 5
        audit_bytes = json.dumps([a.details for a in audits])
        assert sentinel not in audit_bytes
        assert all(set(a.details) <= {"project_id", "environment_id", "revision"} for a in audits)


@pytest.mark.parametrize(
    "body",
    [
        b'{"name":"a","name":"b"}',
        b'{"name":NaN}',
        b'{"name":1}',
        b'{"name":"a","unknown":1}',
        b"[" * 17 + b"0" + b"]" * 17,
    ],
)
def test_bounded_strict_json(client, fleet, body):
    result = client.post(
        f"{fleet}/connections",
        content=body,
        headers={"Content-Type": "application/json", "Idempotency-Key": "strict"},
    )
    assert result.status_code == (413 if body.startswith(b"[") else 400)
    assert "NaN" not in result.text


@pytest.mark.parametrize(
    "headers",
    [
        [("Authorization", "Bearer lp_bad"), ("Authorization", "Bearer lp_other")],
        [("X-Lotuspond-Workspace", "bad"), ("X-Lotuspond-Workspace", "other")],
        [("Content-Encoding", "gzip")],
        [("Idempotency-Key", "one"), ("Idempotency-Key", "two")],
    ],
)
def test_duplicate_and_encoding_headers(client, fleet, headers):
    result = client.post(f"{fleet}/connections", json={"name": "A"}, headers=headers)
    assert result.status_code == 400


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "limit=1.0", "after_id=bad"])
def test_invalid_inventory_query(client, fleet, query):
    assert client.get(f"{fleet}/connections?{query}").status_code == 400


def test_feature_disabled_and_empty_catalog(client, fleet, app):
    item = create(client, fleet)
    assert post(client, f"{fleet}/connections/{item['id']}/versions", CONFIG).status_code == 400
    object.__setattr__(app.state.settings, "fleet_enabled", False)
    assert client.get(f"{fleet}/connections").status_code == 404
    assert client.get("/api/v1/health").status_code == 200


def test_settings_installation_validation():
    from pydantic import ValidationError

    from lotuspond.config import Settings

    for value in [None, "", "bad/path", "x" * 129]:
        with pytest.raises(ValidationError):
            Settings(fleet_enabled=True, fleet_installation_id=value)
    assert Settings(fleet_enabled=True, fleet_installation_id="installation-1").fleet_enabled


def test_catalog_requires_test_mode_and_is_immutable():
    from lotuspond_runtime.protocol import ConnectorPin

    from lotuspond.fleet.catalog import InstalledCatalog

    with pytest.raises(ValueError):
        InstalledCatalog.fixture(ConnectorPin(**PIN), test_mode=False)
    catalog = InstalledCatalog.fixture(ConnectorPin(**PIN), test_mode=True)
    descriptor = catalog.connector(ConnectorPin(**PIN))
    with pytest.raises(TypeError):
        descriptor.operations["write"] = descriptor.operations["inspect"]


def test_secret_replay_rechecks_terminal_revocation(client, fleet, catalog):
    path = f"{fleet}/secret-references"
    original = post(client, path, {"binding_id": "fixture-binding", "version": 1}, "register")
    assert original.status_code == 201
    assert (
        post(client, path, {"binding_id": "fixture-binding", "version": 1}, "register").json()
        == original.json()
    )
    assert post(client, f"{path}/{original.json()['id']}/revoke", {}, "revoke").status_code == 200
    assert (
        post(client, path, {"binding_id": "fixture-binding", "version": 1}, "register").status_code
        == 409
    )


def test_wrong_environment_secret_and_pool_references(client, fleet, catalog, account, db_factory):
    environment = str(uuid4())
    with db_factory.begin() as db:
        db.add(
            models.Environment(
                id=environment,
                workspace_id=account,
                project_id=fleet.split("/")[4],
                name="Other",
                kind="local",
            )
        )
    other = fleet.replace(fleet.split("/")[6], environment)
    ref = post(
        client, f"{other}/secret-references", {"binding_id": "fixture-binding", "version": 1}
    ).json()
    item = create(client, fleet)
    config = dict(CONFIG, secrets=[{"reference_id": ref["id"], "version": 1}])
    wrong_environment_reference = post(client, f"{fleet}/connections/{item['id']}/versions", config)
    assert wrong_environment_reference.status_code == 404
    foreign = create(client, other, key="foreign")
    pool = create(client, fleet, "runtime-pools", "pool")
    policy = dict(POLICY, connection_ids=[foreign["id"]])
    assert post(client, f"{fleet}/runtime-pools/{pool['id']}/policies", policy).status_code == 404
    assert post(client, f"{fleet}/secret-references/{ref['id']}/revoke", {}).status_code == 404
    assert client.get(f"{fleet}/connections?after_id={foreign['id']}").status_code == 404


@pytest.mark.parametrize("corruption", ["digest", "canonical", "links", "policy-links"])
def test_stored_revision_integrity_refused(client, fleet, catalog, db_factory, corruption):
    ref = post(
        client, f"{fleet}/secret-references", {"binding_id": "fixture-binding", "version": 1}
    ).json()
    item = create(client, fleet)
    config = dict(CONFIG, secrets=[{"reference_id": ref["id"], "version": 1}])
    path = f"{fleet}/connections/{item['id']}/versions"
    assert post(client, path, config).status_code == 201
    pool = create(client, fleet, "runtime-pools", "pool")
    policies = f"{fleet}/runtime-pools/{pool['id']}/policies"
    assert (
        post(client, policies, dict(POLICY, connection_ids=[item["id"]]), "policy").status_code
        == 201
    )
    with db_factory.begin() as db:
        revision = db.scalar(select(models.ConnectionVersion))
        if corruption == "digest":
            revision.config_digest = "b" * 64
        elif corruption == "canonical":
            revision.canonical_config = b" " + revision.canonical_config
        elif corruption == "links":
            db.delete(db.scalar(select(models.ConnectionVersionSecret)))
        else:
            db.delete(db.scalar(select(models.RuntimePoolPolicyConnection)))
    if corruption == "policy-links":
        assert client.get(policies).status_code == 503
    else:
        assert client.get(path).status_code == 503
        assert client.get(path + "/1").status_code == 503
        assert (
            post(
                client, policies, dict(POLICY, connection_ids=[item["id"]]), "new-policy"
            ).status_code
            == 503
        )


def test_caller_transaction_rollback_preserves_no_partial_mutation(
    client, fleet, catalog, app, db_factory, account
):
    from lotuspond_runtime.protocol import Scope

    from lotuspond.fleet.management import ConnectionService

    with db_factory() as db:
        with pytest.raises(RuntimeError):
            with db.begin():
                actor = db.scalar(select(models.User)).id
                scope = Scope(
                    workspace_id=account,
                    project_id=fleet.split("/")[4],
                    environment_id=fleet.split("/")[6],
                )
                ConnectionService(db, catalog=app.state.fleet_catalog, actor_id=actor).create(
                    scope, "Rollback", "rollback"
                )
                raise RuntimeError("Synthetic caller rollback")
    with db_factory() as db:
        assert list(db.scalars(select(models.Connection))) == []
        assert list(db.scalars(select(models.FleetMutationRequest))) == []
        assert (
            list(
                db.scalars(
                    select(models.AuditEvent).where(models.AuditEvent.action.like("fleet.%"))
                )
            )
            == []
        )


def test_mfa_session_binding_rechecked(client, fleet, db_factory):
    with db_factory.begin() as db:
        user = db.scalar(select(models.User))
        db.add(
            models.MfaFactor(
                user_id=user.id, secret_ciphertext="synthetic", version=1, enabled_at=models.now()
            )
        )
    assert client.get(f"{fleet}/connections").status_code == 401


@pytest.mark.parametrize("key", [None, "bad/path", "a,b", "x" * 129])
def test_required_bounded_request_keys(client, fleet, key):
    headers = {} if key is None else {"Idempotency-Key": key}
    assert (
        client.post(f"{fleet}/connections", json={"name": "A"}, headers=headers).status_code == 400
    )


def test_fleet_stream_budget_and_safe_error(client, fleet):
    sentinel = b"private-sentinel"
    result = client.post(
        f"{fleet}/connections",
        content=iter([b'{"name":"', sentinel * 5000, b'"}']),
        headers={"Content-Type": "application/json", "Idempotency-Key": "large"},
    )
    assert result.status_code == 413
    assert sentinel.decode() not in result.text


def test_pool_status_and_private_inventory(client, fleet, catalog):
    pool = create(client, fleet, "runtime-pools", "pool")
    path = f"{fleet}/runtime-pools/{pool['id']}"
    assert client.get(f"{fleet}/runtime-pools").json()["items"] == [pool]
    assert post(client, path + "/policies", POLICY, "first").status_code == 201
    first = client.get(path + "/policies").json()["items"][0]
    assert (
        post(client, path + "/policies", dict(POLICY, max_instances=2), "next").status_code == 201
    )
    assert client.get(path).json()["current_version"] == 2
    assert first in client.get(path + "/policies").json()["items"]
    assert post(client, path + "/status", {"status": "disabled"}, "disable").status_code == 200
    assert post(client, path + "/policies", POLICY, "blocked").status_code == 409
    assert post(client, path + "/status", {"status": "active"}, "enable").status_code == 200
    assert post(client, path + "/status", {"status": "revoked"}, "revoke").status_code == 200
    assert post(client, path + "/status", {"status": "active"}, "resurrect").status_code == 409
    ref = post(
        client,
        f"{fleet}/secret-references",
        {"binding_id": "fixture-binding", "version": 1},
        "secret",
    ).json()
    result = client.get(f"{fleet}/secret-references")
    assert result.json() == {"items": [ref], "next_after_id": None}
    assert result.headers["cache-control"] == "no-store"


def test_management_timestamps_preserve_database_utc_offset():
    from datetime import datetime, timedelta, timezone

    from lotuspond.fleet.management import milliseconds

    offset = timezone(timedelta(hours=5, minutes=30))
    row = models.Connection(created_at=datetime(1970, 1, 1, 5, 30, 0, 123999, tzinfo=offset))
    assert milliseconds(row) == 123


def test_foreign_workspace_parent_and_references_not_found(
    client, fleet, catalog, account, db_factory
):
    foreign = {key: str(uuid4()) for key in ("workspace_id", "project_id", "environment_id")}
    connection_id, reference_id = str(uuid4()), str(uuid4())
    with db_factory.begin() as db:
        db.add(
            models.Workspace(
                id=foreign["workspace_id"],
                name="Foreign",
                slug="foreign-reference",
                owner_id=db.get(models.Workspace, account).owner_id,
            )
        )
        db.flush()
        db.add(
            models.Project(
                id=foreign["project_id"], workspace_id=foreign["workspace_id"], name="Foreign"
            )
        )
        db.flush()
        db.add(
            models.Environment(
                id=foreign["environment_id"],
                workspace_id=foreign["workspace_id"],
                project_id=foreign["project_id"],
                name="Foreign",
                kind="local",
            )
        )
        db.flush()
        db.add(models.Connection(**foreign, id=connection_id, name="Foreign"))
        db.add(
            models.SecretReference(
                **foreign, id=reference_id, binding_id="fixture-binding", version=1
            )
        )
    foreign_path = (
        f"/api/v1/projects/{foreign['project_id']}/environments/{foreign['environment_id']}"
    )
    assert client.get(f"{foreign_path}/connections").status_code == 404
    assert client.get(f"{fleet}/connections/{connection_id}").status_code == 404
    item = create(client, fleet)
    config = dict(CONFIG, secrets=[{"reference_id": reference_id, "version": 1}])
    assert post(client, f"{fleet}/connections/{item['id']}/versions", config).status_code == 404
    pool = create(client, fleet, "runtime-pools", "pool")
    assert (
        post(
            client,
            f"{fleet}/runtime-pools/{pool['id']}/policies",
            dict(POLICY, connection_ids=[connection_id]),
        ).status_code
        == 404
    )
