"""The operator authority directory: its tool, its providers and its refusals."""

import json
import os

import pytest
from lotuspond_runtime.protocol import Scope

from lotuspond.authority import admit, init, main, show, switch, trust_publisher
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
