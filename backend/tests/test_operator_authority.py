"""The operator authority directory: its tool, its providers and its refusals."""

import json
import os
from dataclasses import replace

import pytest
from lotuspond_runtime.protocol import Scope

from lotuspond.authority import (
    admit,
    command_key,
    command_trust,
    init,
    install_connector,
    main,
    secret_binding,
    show,
    switch,
    trust_publisher,
)
from lotuspond.config import Settings
from lotuspond.execution.operator_authority import OperatorAuthorities

INSTALLATION = "installation-1"
SCOPE = Scope(
    workspace_id="11111111-1111-4111-8111-111111111111",
    project_id="22222222-2222-4222-8222-222222222222",
    environment_id="33333333-3333-4333-8333-333333333333",
)


@pytest.fixture
def directory(tmp_path):
    root = tmp_path / "authority"
    init(root, INSTALLATION)
    return root


def test_init_writes_private_files_and_starts_with_execution_off(directory):
    for path in [directory, *directory.rglob("*")]:
        assert os.stat(path).st_mode & 0o077 == 0, path
    summary = show(directory)
    assert summary == {
        "installation_id": INSTALLATION,
        "recovery_epoch": 1,
        "execution": "off",
        "release_required": True,
        "trusted_publishers": [],
        "admitted_catalog_documents": 0,
        "command_key": None,
        "installed_connectors": [],
        "secret_bindings": 0,
    }
    authorities = OperatorAuthorities(directory, INSTALLATION)
    assert authorities.entitlement.current(SCOPE, 0).execute is False


def test_init_refuses_a_directory_that_is_not_empty(directory):
    with pytest.raises(SystemExit):
        init(directory, INSTALLATION)


def test_show_never_prints_key_material(directory, capsys):
    main(["show", str(directory)])
    printed = capsys.readouterr().out
    assert (directory / "activity-signing.key").read_text().strip() not in printed
    assert (directory / "artifact-keys" / "primary").read_text().strip() not in printed


def test_each_authority_answers_from_the_directory(directory):
    from lotuspond_sdk.activity import verify_activity

    trust_publisher(directory, "ab" * 32)
    admit(directory, ["cd" * 32])
    switch(directory, "entitlement.json", "execute", True)
    a = OperatorAuthorities(directory, INSTALLATION)
    assert a.recovery.current(INSTALLATION).epoch == 1
    grant = a.entitlement.current(SCOPE, 0)
    assert grant.execute is True and grant.recovery_epoch == 1
    snapshot = a.publishers.current(SCOPE)
    assert snapshot["catalog"] == ["cd" * 32] and snapshot["release_required"] is True
    assert [k["public_key"] for k in snapshot["trust"]["keys"]] == ["ab" * 32]
    assert len(a.keys.key(SCOPE, "primary")) == 32
    assert a.access.current(None, None, None, 0) is False
    assert a.demand.demand("attempt").active_ms == 20000  # 20 s of active time
    assert callable(verify_activity) and a.activity_trust.current(SCOPE, 0).keys


def test_a_new_epoch_and_a_stopped_execution_take_effect_on_the_next_lookup(directory):
    a = OperatorAuthorities(directory, INSTALLATION)
    main(["new-epoch", str(directory)])
    main(["execution", str(directory), "off"])
    assert a.recovery.current(INSTALLATION).epoch == 2
    assert a.entitlement.current(SCOPE, 0).execute is False


def test_another_installation_or_a_readable_file_is_refused(directory):
    with pytest.raises(PermissionError):
        OperatorAuthorities(directory, "installation-2").recovery.current("installation-2")
    os.chmod(directory / "recovery.json", 0o644)
    with pytest.raises(PermissionError):
        OperatorAuthorities(directory, INSTALLATION).recovery.current(INSTALLATION)


def test_a_linked_file_is_refused(directory, tmp_path):
    outside = tmp_path / "elsewhere.json"
    outside.write_text(json.dumps({"epoch": 9, "active": True}))
    os.chmod(outside, 0o600)
    (directory / "recovery.json").unlink()
    (directory / "recovery.json").symlink_to(outside)
    with pytest.raises(OSError):
        OperatorAuthorities(directory, INSTALLATION).recovery.current(INSTALLATION)


def test_the_directory_needs_the_fleet_and_an_absolute_path(tmp_path):
    with pytest.raises(ValueError):
        Settings(env="test", authority_dir=str(tmp_path))  # fleet off
    with pytest.raises(ValueError):
        Settings(
            env="test",
            fleet_enabled=True,
            fleet_installation_id=INSTALLATION,
            authority_dir="relative",
        )


def test_the_api_builds_execution_services_from_the_directory(directory):
    from lotuspond.app import create_app

    # Built from settings, not injected: the same path production takes (SQLite is
    # allowed only in test settings, and nothing here touches the database).
    settings = Settings(
        env="test",
        database_url="sqlite+pysqlite:///:memory:",
        fleet_enabled=True,
        fleet_installation_id=INSTALLATION,
        authority_dir=str(directory),
    )
    app = create_app(settings)
    assert app.state.execution_services is not None
    assert app.state.fleet_dependencies.recovery.current(INSTALLATION).epoch == 1
    assert (
        create_app(settings.model_copy(update={"authority_dir": None})).state.execution_services
        is None
    )


def test_the_worker_builds_its_engine_from_the_directory(directory):
    from lotuspond.execution_worker import ExecutionWorkerSettings, WorkerRefused, production_engine

    base = {
        "database_url": "postgresql+psycopg://lotuspond@127.0.0.1:5432/x",
        "worker_id": "worker-1",
        "role": "orchestrator",
        "interpreter_ids": ["lotuspond.execution-interpreter.v1"],
    }
    with pytest.raises(WorkerRefused):
        production_engine(ExecutionWorkerSettings(**base))
    engine = production_engine(
        ExecutionWorkerSettings(**base, authority_dir=str(directory), installation_id=INSTALLATION)
    )
    assert "lotuspond.execution-interpreter.v1" in engine.interpreter_ids


# ---------------------------------------------------------------- E05 actions

V1 = "lotuspond.execution-interpreter.v1"
V2 = "lotuspond.execution-interpreter.v2"
CONNECTOR = {
    "pin": {"name": "erp", "version": "1.0.0", "artifact_sha256": "ef" * 32},
    "operations": {
        "get_order": {"effect": "read", "requires_destination": True, "requires_secret": True},
        "update_order": {
            "effect": "write",
            "requires_destination": True,
            "requires_secret": True,
        },
    },
}


def _command():
    import importlib.util
    import time
    from pathlib import Path

    path = Path(__file__).parents[2] / "runtime/python/tests/command_fixtures.py"
    spec = importlib.util.spec_from_file_location("runtime_command_fixtures", path)
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    return helpers.command_case(now=time.time_ns() // 1000000).command


def test_without_a_command_key_only_interpreter_v1_runs(directory):
    from lotuspond.execution.composition import compose
    from lotuspond.security import RateLimiter

    authorities = OperatorAuthorities(directory, INSTALLATION)
    assert authorities.commands is None
    graph = compose(
        authorities,
        engine=None,
        installation_id=INSTALLATION,
        limiter=RateLimiter(Settings(env="development", redis_url=None)),
    )
    assert graph.interpreter_ids == (V1,)
    assert graph.services.actions is None


def test_a_provisioned_command_key_switches_on_actions(directory):
    from lotuspond.execution.composition import compose
    from lotuspond.security import RateLimiter

    command_key(directory)
    for path in [directory / "command-signing.key", directory / "command-trust.json"]:
        assert os.stat(path).st_mode & 0o077 == 0
    with pytest.raises(SystemExit):
        command_key(directory)  # never replaced in place
    authorities = OperatorAuthorities(directory, INSTALLATION)
    assert authorities.commands is not None and authorities.commands.test_only is False
    graph = compose(
        authorities,
        engine=None,
        installation_id=INSTALLATION,
        limiter=RateLimiter(Settings(env="development", redis_url=None)),
    )
    assert graph.interpreter_ids == (V1, V2)
    assert graph.services.actions is not None
    summary = show(directory)
    assert summary["command_key"] == [command_trust(directory)["trust"]["keys"][0]["key_id"]]


def test_the_command_key_signs_what_its_trust_verifies(directory):
    import time

    from lotuspond_sdk.executable.signing import verify_runtime_command

    command_key(directory)
    key = OperatorAuthorities(directory, INSTALLATION).commands
    snapshot = key.current(INSTALLATION)
    now = time.time_ns() // 1_000_000
    assert snapshot.revision == 1
    assert now < snapshot.valid_until_ms <= now + 60_000  # inside E03's bound
    command = _command()
    envelope = key.sign(command, snapshot)
    assert verify_runtime_command(command, envelope, snapshot.trust).valid


def test_the_command_key_refuses_without_its_trust(directory):
    import json as _json

    from lotuspond_runtime.errors import FleetError

    command_key(directory)
    key = OperatorAuthorities(directory, INSTALLATION).commands
    with pytest.raises(FleetError, match="trust_unavailable"):
        key.current("another-installation")
    snapshot = key.current(INSTALLATION)
    # A key no longer allowed by the trust signs nothing, on the next call.
    state = _json.loads((directory / "command-trust.json").read_text())
    state["trust"]["keys"][0]["state"] = "revoked"
    path = directory / "command-trust.json"
    path.write_text(_json.dumps(state))
    with pytest.raises(FleetError, match="trust_unavailable"):
        key.sign(_command(), key.current(INSTALLATION))
    (directory / "command-trust.json").unlink()
    with pytest.raises(FleetError, match="trust_unavailable"):
        key.current(INSTALLATION)
    # An expired snapshot signs nothing either.
    with pytest.raises(FleetError, match="trust_unavailable"):
        key.sign(_command(), replace(snapshot, valid_until_ms=1))


def test_show_never_prints_the_command_key(directory, capsys):
    command_key(directory)
    main(["show", str(directory)])
    assert (directory / "command-signing.key").read_text().strip() not in capsys.readouterr().out
    main(["command-trust", str(directory)])
    printed = capsys.readouterr().out
    assert (directory / "command-signing.key").read_text().strip() not in printed
    assert "public_key" in printed


def test_installed_connectors_and_bindings_form_the_catalog(directory):
    from lotuspond_runtime.errors import FleetError
    from lotuspond_runtime.protocol import ConnectorPin

    assert OperatorAuthorities(directory, INSTALLATION).catalog.test_only is False
    install_connector(directory, CONNECTOR)
    install_connector(directory, CONNECTOR)  # the same description again is a no-op
    changed = {**CONNECTOR, "operations": {"get_order": CONNECTOR["operations"]["get_order"]}}
    with pytest.raises(SystemExit):
        install_connector(directory, changed)
    with pytest.raises(SystemExit):
        install_connector(directory, {"pin": CONNECTOR["pin"]})
    secret_binding(directory, "erp_token", 1)
    with pytest.raises(SystemExit):
        secret_binding(directory, "not valid!", 1)
    catalog = OperatorAuthorities(directory, INSTALLATION).catalog
    pin = ConnectorPin.model_validate(CONNECTOR["pin"])
    assert catalog.connector(pin).operations["update_order"].effect == "write"
    assert catalog.secret_binding("erp_token", 1)
    assert not catalog.secret_binding("erp_token", 2)
    with pytest.raises(FleetError):
        catalog.connector(ConnectorPin.model_validate({**CONNECTOR["pin"], "version": "2.0.0"}))
    assert show(directory)["installed_connectors"] == ["erp@1.0.0"]
    assert show(directory)["secret_bindings"] == 1


def test_a_malformed_catalog_refuses_to_start(directory):
    (directory / "catalog.json").write_text('{"connectors": []}')
    os.chmod(directory / "catalog.json", 0o600)
    with pytest.raises(ValueError):
        OperatorAuthorities(directory, INSTALLATION)


def test_the_worker_and_api_run_actions_with_a_command_key(directory):
    from lotuspond.app import create_app
    from lotuspond.execution_worker import ExecutionWorkerSettings, production_engine

    command_key(directory)
    install_connector(directory, CONNECTOR)
    engine = production_engine(
        ExecutionWorkerSettings(
            database_url="postgresql+psycopg://lotuspond@127.0.0.1:5432/x",
            worker_id="worker-1",
            role="orchestrator",
            interpreter_ids=[V1, V2],
            authority_dir=str(directory),
            installation_id=INSTALLATION,
        )
    )
    assert set(engine.interpreter_ids) == {V1, V2}
    app = create_app(
        Settings(
            env="test",
            database_url="sqlite+pysqlite:///:memory:",
            fleet_enabled=True,
            fleet_installation_id=INSTALLATION,
            authority_dir=str(directory),
        )
    )
    assert app.state.execution_services.actions is not None
    from lotuspond_runtime.protocol import ConnectorPin

    assert app.state.fleet_dependencies.catalog.connector(
        ConnectorPin.model_validate(CONNECTOR["pin"])
    )
