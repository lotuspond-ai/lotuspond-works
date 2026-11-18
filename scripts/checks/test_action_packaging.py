"""E05 Task 13: reviewed actions keep their packaging boundaries.

No database is needed. Production settings must leave the synthetic target's
providers and the test command key unselectable, a missing command key must leave
action dispatch unavailable rather than ready, the installed acceptance report must
refuse to pass without every Task 12 action scenario, and the capability text must
claim synthetic evidence only.
"""

import copy
import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
INSTALLATION = "installation-1"
V1 = "lotuspond.execution-interpreter.v1"
V2 = "lotuspond.execution-interpreter.v2"

sys.path.insert(0, str(BACKEND))


def smoke():
    spec = importlib.util.spec_from_file_location(
        "execution_smoke", ROOT / "scripts/checks/execution-smoke.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / "scripts/checks"))
    spec.loader.exec_module(module)
    return module


def operator_authorities(tmp_path):
    """The production authorities, from an operator directory (as the API builds them)."""
    from lotuspond.authority import init
    from lotuspond.execution.operator_authority import OperatorAuthorities

    root = tmp_path / "authority"
    init(root, INSTALLATION)
    return OperatorAuthorities(root, INSTALLATION)


def compose(authorities):
    from lotuspond.execution.composition import compose as build

    # Nothing here touches the database: composition only wires services.
    return build(
        authorities,
        engine=create_engine("sqlite+pysqlite:///:memory:"),
        installation_id=INSTALLATION,
        limiter=None,
        test_mode=False,
    )


class Key:
    """A command key provider's shape; `test_only` marks a test world's file key."""

    def __init__(self, test_only):
        self.test_only = test_only

    def sign(self, *args, **kwargs):  # never called: composition only wires it
        raise AssertionError("signed during composition")

    def current(self, *args, **kwargs):
        raise AssertionError("read trust during composition")


def synthetic_write():
    from lotuspond_runtime.action_invocation import WriteDescriptor
    from lotuspond_runtime.protocol import ConnectorPin

    return WriteDescriptor(
        "synthetic-target-write",
        ConnectorPin(
            name="synthetic-target", version="1.0.0", artifact_sha256="e" * 64
        ),
        {"submit": "target_key"},
        test_only=True,
        script={"ca_file": "/nonexistent/ca.pem", "egress": [], "loopback": True},
    )


def synthetic_read():
    from lotuspond_runtime.protocol import ConnectorPin
    from lotuspond_runtime.providers import ProviderDescriptor
    from lotuspond_sdk.executable.package import ArtifactRef

    return ProviderDescriptor(
        "synthetic-target-read",
        ArtifactRef(
            kind="connector", name="synthetic-target", version="1.0.0", sha256="c" * 64
        ),
        "read",
        frozenset({"lookup"}),
        test_only=True,
        script={"ca_file": "/nonexistent/ca.pem"},
        connector=ConnectorPin(
            name="synthetic-target", version="1.0.0", artifact_sha256="e" * 64
        ),
    )


def test_action_fixtures_unselectable_in_production(tmp_path):
    from lotuspond.fleet.catalog import InstalledCatalog
    from lotuspond_runtime import action_invocation
    from lotuspond_runtime.action_invocation import WriteProviderSet
    from lotuspond_runtime.providers import ProviderSet

    descriptor = synthetic_write()
    # Runtime: a production worker cannot be configured with the synthetic writer...
    with pytest.raises(ValueError, match="Synthetic providers require test mode"):
        WriteProviderSet((descriptor,), test_mode=False)
    # ...nor with the synthetic target's read provider (read-backs, verification)...
    read = synthetic_read()
    with pytest.raises(ValueError, match="Synthetic providers require test mode"):
        ProviderSet((read,), test_mode=False)
    assert ProviderSet((read,), test_mode=True) is not None  # positive control
    # ...and its provider child, handed one anyway, answers unavailable unreached.
    answer = action_invocation._answer(
        {
            "descriptor": descriptor.to_json(),
            "secrets": [],
            "test_mode": False,
            "records": None,
            "idempotency_key": None,
            "deadline_ms": 0,
        }
    )
    production_provider_selection_error = type(
        "Refusal", (), {"code": answer["failure"], "reached": not answer["not_reached"]}
    )
    assert production_provider_selection_error.code == "unavailable"
    assert production_provider_selection_error.reached is False
    # Positive control: in test mode the same descriptor selects the synthetic writer.
    assert WriteProviderSet((descriptor,), test_mode=True) is not None
    assert action_invocation._provider(descriptor, (), True) is not None

    # Server: production composition refuses the test command key and the test
    # catalog that names the synthetic target.
    base = operator_authorities(tmp_path)
    keyed = copy.copy(base)
    keyed.commands = Key(test_only=True)
    with pytest.raises(ValueError, match="A test command key needs test mode"):
        compose(keyed)
    catalogued = copy.copy(base)
    catalogued.catalog = InstalledCatalog.fixture(
        {"name": "lotuspond-fixture", "version": "1.0.0", "artifact_sha256": "a" * 64},
        test_mode=True,
    )
    with pytest.raises(ValueError, match="A test catalog needs test mode"):
        compose(catalogued)
    # Positive control: the operator's own authorities compose.
    assert compose(base).services is not None


def test_missing_command_key_is_not_readiness_success(tmp_path, monkeypatch):
    from lotuspond import execution_worker
    from lotuspond.execution.boundary import action_services
    from lotuspond.execution.errors import ExecutionError
    from lotuspond.execution_worker import (
        ExecutionWorkerSettings,
        WorkerRefused,
        production_engine,
    )

    graph = compose(operator_authorities(tmp_path))
    dispatch_readiness_without_key = graph.services.actions is not None
    assert dispatch_readiness_without_key is False
    assert graph.interpreter_ids == (V1,)

    class Request:
        class app:
            class state:
                execution_services = graph.services

    # The action and task routes refuse (503) rather than answer.
    with pytest.raises(ExecutionError) as refused:
        action_services(Request())
    assert refused.value.code == "authority_unavailable"
    # A worker asked to serve interpreter v2 refuses to start without the key. The
    # database check is stubbed so the refusal under test is the interpreter's.
    monkeypatch.setattr(
        execution_worker, "_verify_session", lambda database, settings: None
    )
    settings = {
        "database_url": "postgresql+psycopg://lotuspond@127.0.0.1:1/x",
        "worker_id": "worker-1",
        "role": "orchestrator",
        "authority_dir": str(tmp_path / "authority"),
        "installation_id": INSTALLATION,
    }
    with pytest.raises(WorkerRefused) as unsupported:
        execution_worker.run_execution_worker(
            ExecutionWorkerSettings(**settings, interpreter_ids=[V1, V2])
        )
    assert unsupported.value.code == "unsupported_interpreter"
    assert production_engine(ExecutionWorkerSettings(**settings, interpreter_ids=[V1]))
    # Positive control: an operator's (non-test) key provider is what wires dispatch.
    keyed = copy.copy(operator_authorities(tmp_path / "keyed"))
    keyed.commands = Key(test_only=False)
    with_key = compose(keyed)
    assert with_key.services.actions is not None
    assert with_key.interpreter_ids == (V1, V2)


ACTION_SCENARIOS = (
    "reviewed_write_result_and_counts",
    *(
        f"rollback_action_{point}_{when}_commit"
        for point in (
            "intent",
            "decision",
            "dispatch",
            "action_claim",
            "receipt",
            "readback",
            "outcome",
        )
        for when in ("before", "after")
    ),
)


def test_required_action_scenarios_cannot_skip(tmp_path):
    from test_execution_packaging import complete_report

    smoke_module = smoke()
    assert set(ACTION_SCENARIOS) <= set(smoke_module.REQUIRED_SCENARIOS)
    report = complete_report(smoke_module)
    assert smoke_module.require_report(report) is report  # positive control
    for scenario in ACTION_SCENARIOS:
        for variant in (0, 1):
            missing = copy.deepcopy(report)
            journey = missing["journeys"][variant]
            journey["scenarios"] = [
                s for s in journey["scenarios"] if s["name"] != scenario
            ]
            with pytest.raises(smoke_module.EvidenceError) as error:
                smoke_module.require_report(missing)
            required_report_missing_error = error.value
            assert required_report_missing_error.code == "missing_required_evidence"
        failed = copy.deepcopy(report)
        for item in failed["journeys"][0]["scenarios"]:
            if item["name"] == scenario:
                item["passed"] = False
        with pytest.raises(smoke_module.EvidenceError):
            smoke_module.require_report(failed)
    # A report without the write count is no evidence of at most one write.
    uncounted = copy.deepcopy(report)
    del uncounted["journeys"][0]["max_target_writes_per_intent"]
    with pytest.raises(smoke_module.EvidenceError):
        smoke_module.require_report(uncounted)
    # Required mode fails, never skips, an action case whose prerequisite is missing.
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in ("LOTUSPOND_PLATFORM_TEST_DATABASE_URL",)
    }
    environment["LOTUSPOND_REQUIRE_DURABLE_EXECUTION_TESTS"] = "1"
    case = (
        "tests/execution/conformance/actions/test_reviews.py"
        "::test_act_1_only_an_eligible_independent_reviewer_decides"
    )
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", case]
        + ["--basetemp", str(tmp_path / "required")],
        cwd=BACKEND,
        env=environment,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert done.returncode != 0, done.stdout[-1000:]
    assert "required durable execution prerequisite missing" in done.stdout
    assert " skipped" not in done.stdout.splitlines()[-1]


def capability_row(name):
    for line in (ROOT / "docs/CAPABILITIES.md").read_text().splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == 3 and cells[0] == name:
            return cells
    return None


def test_capabilities_do_not_claim_live_writes_or_unbuilt_work():
    """The E05 row claims what is built and no more.

    Since 2026-11-15 the row rightly names the console's Human tasks inbox and the
    production wiring (an operator installs real connectors; a runtime outside test mode
    runs write providers). What it must not claim is a write to a real customer system,
    readiness, live providers, notifications or compensation: its evidence stays local
    and synthetic, and what remains says so.
    """
    row = capability_row("Reviewed actions and human tasks (E05)")
    assert row is not None, "CAPABILITIES.md has no E05 row"
    _, boundary, remaining = (cell.lower() for cell in row)
    # The boundary names its evidence as synthetic and local.
    assert "synthetic" in boundary and "local" in boundary
    for claim in (
        "production-ready",
        "production ready",
        "live",
        "real system",
        "real customer system",
        "notif",
        "compensat",
    ):
        assert claim not in boundary, claim
    # What remains says so plainly.
    for gap in (
        r"\breal customer system\b",
        "notification",
        "compensation",
        "hosted ci",
    ):
        assert re.search(gap, remaining), gap
    # Human tasks is a console page now, no longer a planned placeholder.
    roadmap = (ROOT / "web/src/lib/roadmap.ts").read_text()
    assert "id: 'human-tasks'" not in roadmap
    navigation = (ROOT / "web/src/components/console/navigation.tsx").read_text()
    assert "id: 'human-tasks'" in navigation
    assert (ROOT / "web/src/pages/HumanTasks.tsx").is_file()
