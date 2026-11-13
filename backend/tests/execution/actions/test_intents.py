"""E05 Task 4: action intents as deterministic interpreter-v2 row transitions.

Golden v2 fixtures are recorded explicitly, never by a test run:
`uv run --frozen python -m tests.execution.actions.test_intents` (from backend/).
"""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from lotuspond_runtime.protocol import (
    ConnectionConfig,
    canonical_connection_json,
    connection_digest,
)
from lotuspond_sdk.executable import validate_executable_package
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from lotuspond.execution import action_contracts as ac
from lotuspond.execution.action_contracts import INTERPRETER_V2
from lotuspond.execution.contracts import AdmissionRequest
from lotuspond.execution.engine_contracts import INTERPRETER_V1, WorkerIdentity
from lotuspond.execution.orchestrator import mark_runnable
from lotuspond.execution.program import program_from
from lotuspond.execution.progression import (
    CreateIntent,
    InterpreterV2,
    _RegionV2,
    derivation_json,
    derive,
    state_from_json,
)
from lotuspond.models import (
    ConnectionVersion,
    ExecutablePublication,
    Execution,
    ExecutionActionIntent,
    ExecutionArtifact,
    ExecutionDeploymentRevision,
    ExecutionEvent,
)

from ..engine_fixtures import FixtureFence, FixtureSweeper
from ..test_admission import admission as admission  # noqa: F401
from ..test_budgets import budget_harness as budget_harness  # noqa: F401
from ..test_progression import flows as flows  # noqa: F401
from ..test_publication import harness as harness  # noqa: F401
from .test_admission import deploy, doc, enable, publish

FIXTURES = Path(__file__).parents[1] / "fixtures"
GOLDEN = FIXTURES / "progression" / INTERPRETER_V2
WORKER = WorkerIdentity(
    worker_id="interpreter-v2",
    role="orchestrator",
    interpreter_ids=[INTERPRETER_V1, INTERPRETER_V2],
)
TASK1 = json.loads((Path(__file__).parent / "fixtures" / "records.json").read_text())
STRING = {"kind": "string", "max_length": 128}


@pytest.fixture
def intents(flows):
    from lotuspond.execution.engine import PostgresExecutionEngine

    h = flows
    h.v2 = InterpreterV2(
        artifacts=h.store,
        artifact_policy=h.policy,
        budgets=h.budgets,
        recovery=FixtureFence(),
        installation_id="test-installation",
        test_mode=True,
    )
    h.engine_v2 = PostgresExecutionEngine(
        h.v2,
        FixtureSweeper(),
        FixtureFence(),
        interpreter_ids=[INTERPRETER_V1, INTERPRETER_V2],
        test_mode=True,
    )

    def run(package, value=None, key="run", documents=None, before_drive=None):
        documents = documents or [doc("connector"), doc("reviewers")]
        with Session(h.engine) as db, db.begin():
            publication = publish(h, db, package, documents, key)
            view = deploy(h, db, publication, documents, key, package=package)
            enable(h, db, view, f"{key}-on")
        with Session(h.engine) as db, db.begin():
            admitted = h.service.admit_execution(
                db,
                h.actor,
                AdmissionRequest(deployment_revision_id=view.revision_id, input=value),
                key,
            )
        if before_drive is not None:
            with Session(h.engine) as db, db.begin():
                before_drive(db, view)
        h.drive2()
        return admitted.id

    def drive2(rounds=50):
        for _ in range(rounds):
            if h.engine_v2.progress(h.sessions, WORKER, limit=10).claimed == 0:
                break

    h.run, h.drive2 = run, drive2
    return h


def intent_then_pause(target=None, input=None, items=None):
    package = doc("reviewed_action")
    intent = deepcopy(package["body"]["steps"][0])
    if target is not None:
        intent["target"] = target
    if input is not None:
        intent["input"] = input
    pause = {"kind": "timer", "id": "pause", "delay_seconds": 600}
    if items is None:
        package["body"]["steps"] = [intent, pause]
    else:
        package["body"]["steps"] = [
            {
                "kind": "iteration",
                "id": "each",
                "input": {
                    "kind": "literal",
                    "schema": {"kind": "array", "items": STRING, "max_items": len(items)},
                    "value": items,
                },
                "max_items": len(items),
                "concurrency": len(items),
                "body": {
                    "steps": [intent],
                    "result": {"kind": "literal", "schema": {"kind": "null"}, "value": None},
                    "result_schema": {"kind": "data", "schema": {"kind": "null"}},
                },
            },
            pause,
        ]
    return package


def event_details(h, db, execution_id, code):
    """The decrypted `detail` of each control event with this code."""
    found = []
    for event in db.scalars(
        select(ExecutionEvent)
        .where(ExecutionEvent.execution_id == execution_id, ExecutionEvent.code == code)
        .order_by(ExecutionEvent.sequence)
    ):
        content = h.store._get_internal(
            db,
            h.scope,
            {
                "id": event.event_artifact_id,
                "version": event.event_artifact_version,
                "sha256": event.digest,
            },
        )
        found.append(json.loads(content)["detail"])
    return found


def rows(h, model, execution_id):
    with Session(h.engine) as db:
        return list(db.scalars(select(model).where(model.execution_id == execution_id)))


def test_binding_comes_from_records_only(intents):
    h = intents
    # The Task 1 vector: these records give exactly the frozen intent digest.
    fields = ac.BindingFields(**TASK1["binding_fields"])
    assert ac.derive_intent(fields).sha256 == TASK1["expected"]["intent_sha256"]
    derived_intent_sha256 = ac.derive_intent(fields).sha256
    assert (
        derived_intent_sha256 == "2c976a61d1ad7c137b0e453862854019b3c47c575f9e6f07303efd6fb0da81bd"
    )
    # A value reaches the target only through the compiled mapping: here from the
    # execution input, which chooses nothing else.
    package = intent_then_pause(
        target={
            "kind": "object",
            "fields": [
                {
                    "name": "record_id",
                    "value": {
                        "kind": "ref",
                        "source": {"kind": "package_input"},
                        "pointer": "/record_id",
                    },
                }
            ],
        }
    )
    package["input_schema"] = {
        "kind": "object",
        "fields": [{"name": "record_id", "schema": STRING, "required": True}],
    }
    execution_id = h.run(package, {"record_id": "R-7"})
    (row,) = rows(h, ExecutionActionIntent, execution_id)
    binding = json.loads(row.binding_bytes)
    with Session(h.engine) as db:
        execution = db.get(Execution, execution_id)
        revision = db.get(ExecutionDeploymentRevision, execution.deployment_revision_id)
        publication = db.get(ExecutablePublication, revision.publication_id)
        target = json.loads(
            h.store._get_internal(
                db,
                h.scope,
                {
                    "id": row.target_artifact_id,
                    "version": row.target_artifact_version,
                    "sha256": row.target_sha256,
                },
            )
        )
    connector, policy = doc("connector"), doc("reviewers")
    from lotuspond_sdk.executable.catalog import catalog_document_digest

    assert target == {"record_id": "R-7"}
    assert binding == {
        **binding,
        "installation_id": "test-installation",
        "recovery_epoch": 1,
        "workspace_id": execution.workspace_id,
        "project_id": execution.project_id,
        "environment_id": execution.environment_id,
        "execution_id": execution.id,
        "step_id": "intent",
        "package_sha256": publication.package_sha256,
        "plan_sha256": publication.plan_sha256,
        "configuration_sha256": revision.configuration_sha256,
        "connector_name": "synthetic-target",
        "connector_version": "1.0.0",
        "connector_artifact_sha256": connector["definition"]["artifact_sha256"],
        "operation": "submit",
        "policy_sha256": catalog_document_digest(policy),
        "approval_mode": "human",
        "independent_approval": True,
        "requester_id": execution.initiating_user_id,
    }
    assert row.authorization_state == "awaiting_review" and row.effect_state == "not_dispatched"
    assert (
        row.intent_sha256
        == ac.derive_intent(
            ac.BindingFields(
                **{
                    k: v
                    for k, v in binding.items()
                    if k not in {"format_version", "record_type", "expires_at_ms"}
                },
                expiry_seconds=3600,
                execution_deadline_ms=execution.deadline_ms,
            )
        ).sha256
    )
    with Session(h.engine) as db:
        assert db.get(Execution, execution_id).state == "waiting"  # on to the timer
        codes = list(
            db.scalars(
                select(ExecutionEvent.code)
                .where(ExecutionEvent.execution_id == execution_id)
                .order_by(ExecutionEvent.sequence)
            )
        )
    assert "intent_created" in codes


def test_intent_is_immutable_and_unique_per_occurrence(intents):
    h = intents
    execution_id = h.run(intent_then_pause())
    (row,) = rows(h, ExecutionActionIntent, execution_id)
    with Session(h.engine) as db, db.begin():
        execution = db.get(Execution, execution_id, with_for_update=True)
        before = db.scalar(select(func.count()).select_from(ExecutionActionIntent))
        # Re-running the committed transition creates nothing.
        applied = h.v2._create_intent(
            db,
            execution,
            1,
            CreateIntent(
                "intent", "intent", {"record_id": "R-100"}, {"amount_minor": 1, "currency": "EUR"}
            ),
        )
        rows_created_by_repeated_transition = (
            db.scalar(select(func.count()).select_from(ExecutionActionIntent)) - before
        )
        assert applied is False and rows_created_by_repeated_transition == 0
    from sqlalchemy.exc import IntegrityError

    with Session(h.engine) as db, db.begin(), pytest.raises(IntegrityError):
        copy = {c.name: getattr(row, c.name) for c in ExecutionActionIntent.__table__.columns}
        copy.update(
            id="00000000-0000-4000-8000-0000000000ff",
            action_id="00000000-0000-4000-8000-0000000000fe",
            intent_sha256="9" * 64,
        )
        db.execute(ExecutionActionIntent.__table__.insert().values(**copy))


@pytest.mark.parametrize(
    "tamper", ["binding_byte", "artifact_byte", "input_byte", "digest", "rebound"]
)
def test_tampered_intent_or_artifact_denies(intents, tamper):
    h = intents
    execution_id = h.run(intent_then_pause(), key=f"tamper-{tamper}")
    (row,) = rows(h, ExecutionActionIntent, execution_id)
    with Session(h.engine) as db, db.begin():
        intent = db.get(ExecutionActionIntent, row.id)
        if tamper == "binding_byte":
            intent.binding_bytes = intent.binding_bytes.replace(b'"R', b'"r', 1).replace(
                b'"submit"', b'"submiT"'
            )
        elif tamper == "digest":
            intent.intent_sha256 = "0" * 64
        elif tamper == "rebound":
            # Rewrite bytes and digest consistently: only the pinned records catch it.
            binding = json.loads(intent.binding_bytes)
            binding["operation"] = "lookup"
            from lotuspond_sdk.executable.actions import build_action_intent, canonical_binding_json

            rebuilt = build_action_intent(binding)
            intent.binding_bytes = canonical_binding_json(rebuilt.binding)
            intent.intent_sha256 = rebuilt.sha256
        else:
            artifact_id = (
                intent.target_artifact_id if tamper == "artifact_byte" else intent.input_artifact_id
            )
            artifact = db.get(ExecutionArtifact, artifact_id)
            artifact.ciphertext = bytes([artifact.ciphertext[0] ^ 1]) + artifact.ciphertext[1:]
        execution = db.get(Execution, execution_id, with_for_update=True)
        mark_runnable(db, execution, 0)
    h.drive2()
    with Session(h.engine) as db:
        tampered_execution = db.get(Execution, execution_id)
        assert tampered_execution.state == "reconciliation_required"
        assert event_details(h, db, execution_id, "reconciliation_required") == ["integrity"]


def test_iteration_occurrences_get_distinct_actions(intents):
    h = intents
    item = {"kind": "ref", "source": {"kind": "iteration_item"}, "pointer": ""}
    package = intent_then_pause(
        target={"kind": "object", "fields": [{"name": "record_id", "value": item}]},
        items=["R-1", "R-2", "R-3"],
    )
    execution_id = h.run(package)
    iteration_intents = rows(h, ExecutionActionIntent, execution_id)
    assert len({i.action_id for i in iteration_intents}) == 3
    assert len({i.intent_sha256 for i in iteration_intents}) == 3
    assert len({i.step_instance_id for i in iteration_intents}) == 3


def test_expiry_is_capped_by_the_deadline(intents):
    h = intents
    package = intent_then_pause()
    package["limits"]["elapsed_seconds"] = 900  # under the intent's 3600 s expiry
    execution_id = h.run(package)
    (row,) = rows(h, ExecutionActionIntent, execution_id)
    with Session(h.engine) as db:
        execution = db.get(Execution, execution_id)
    assert row.expires_at_ms <= execution.deadline_ms
    assert row.expires_at_ms == execution.deadline_ms


def test_connection_artifact_mismatch_denies_at_creation(intents):
    h = intents

    def drift(db, view):
        # The deployed connection version's bytes change after deployment.
        revision = db.get(ExecutionDeploymentRevision, view.revision_id)
        version = db.scalar(
            select(ConnectionVersion).where(
                ConnectionVersion.version == h.connection_versions,
                ConnectionVersion.workspace_id == revision.workspace_id,
            )
        )
        config = ConnectionConfig.model_validate_json(version.canonical_config)
        other = config.connector.model_copy(update={"artifact_sha256": "f" * 64})
        config = config.model_copy(update={"connector": other})
        version.canonical_config = canonical_connection_json(config)
        version.config_digest = connection_digest(config)

    execution_id = h.run(intent_then_pause(), key="drift", before_drive=drift)
    assert rows(h, ExecutionActionIntent, execution_id) == []
    with Session(h.engine) as db:
        execution = db.get(Execution, execution_id)
        assert execution.state == "failed"
        assert event_details(h, db, execution_id, "attempt_failed") == ["binding_refused"]


# ---------------------------------------------------------------- golden v2 fixtures

NOW = 1_800_000_000_000
EXECUTION = "00000000-0000-4000-8000-000000000001"


def golden_program():
    package = intent_then_pause()
    sources = {0: doc("connector"), 1: doc("reviewers")}
    return program_from(validate_executable_package(package), sources), package


def golden_cases():
    handle = {"handle_kind": "action_intent", "id": "intent-1"}
    base = {
        "interpreter": INTERPRETER_V2,
        "execution_id": EXECUTION,
        "region_path": "",
        "requested_state": "running",
        "deadline_ms": NOW + 86_400_000,
        "input": None,
        "now_ms": NOW,
    }
    done = {
        "path": "intent",
        "attempts": [{"id": "a-intent", "ordinal": 1, "state": "succeeded", "value": handle}],
    }
    refused = {
        "path": "intent",
        "attempts": [
            {
                "id": "a-intent",
                "ordinal": 1,
                "state": "failed",
                "failure": "binding_refused",
                "failed_at_ms": NOW,
            }
        ],
    }
    return [
        {**base, "name": "intent-create", "occurrences": []},
        {**base, "name": "intent-done-opens-timer", "occurrences": [done]},
        {**base, "name": "intent-refused-fails", "occurrences": [refused]},
    ]


def derive_bytes(case):
    program, _ = golden_program()
    state = state_from_json(program, case)
    return derivation_json(derive(program, state, case["now_ms"], _RegionV2))


def test_v2_golden_fixtures_rederive():
    files = sorted(GOLDEN.glob("*.json"))
    assert {p.stem for p in files} == {c["name"] for c in golden_cases()}
    for path in files:
        record = json.loads(path.read_text())
        assert record["interpreter"] == INTERPRETER_V2
        from lotuspond.execution.contracts import canonical_value

        assert derive_bytes(record) == canonical_value(record["derivation"]), path.name


def record():
    GOLDEN.mkdir(parents=True, exist_ok=True)
    for case in golden_cases():
        content = {**case, "derivation": json.loads(derive_bytes(case))}
        (GOLDEN / f"{case['name']}.json").write_text(
            json.dumps(content, indent=1, sort_keys=True) + "\n"
        )


if __name__ == "__main__":
    record()
