"""Independent behavioral schema validation and committed byte parity."""

import copy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from lotuspond_sdk.executable.compiler import compile_package
from lotuspond_sdk.executable.plan import canonical_plan_json
from lotuspond_sdk.executable.schema import export_schema, schema_bytes
from lotuspond_sdk.executable.wire import event_payload_digest


def test_package_plan_and_committed_parity(case):
    import json

    root = Path(__file__).resolve().parents[4] / "contracts"
    names = {
        "package": "workflow-package.v2",
        "plan": "compiled-workflow.v2",
        "runtime": "runtime-contracts.v1",
    }
    for kind, name in names.items():
        schema = export_schema(kind)
        Draft202012Validator.check_schema(schema)
        assert (root / f"{name}.schema.json").read_bytes() == schema_bytes(kind)
    package = case("minimal")
    validator = Draft202012Validator(export_schema("package"))
    validator.validate(package)
    bad = copy.deepcopy(package)
    bad["limits"]["max_steps"] = True
    assert not validator.is_valid(bad)
    plan = json.loads(
        canonical_plan_json(compile_package(package, dependencies=[], catalog={"documents": []}))
    )
    Draft202012Validator(export_schema("plan")).validate(plan)
    plan["authority_granted"] = True
    assert not Draft202012Validator(export_schema("plan")).is_valid(plan)


@pytest.mark.parametrize(
    "kind,payload,step,action",
    [
        ("execution_completed", {"output_sha256": "1" * 64}, False, False),
        ("step_started", {"input_sha256": "1" * 64}, True, False),
        ("dispatch_timeout", {"intent_sha256": "1" * 64, "may_have_dispatched": True}, True, True),
    ],
)
def test_event_payload_and_reference_correlation(kind, payload, step, action):
    event = {
        "record_type": "runtime_event",
        "format_version": 1,
        "event_id": "event-1",
        "installation_id": "installation-1",
        "recovery_epoch": 1,
        "workspace_id": "workspace-1",
        "project_id": "project-1",
        "environment_id": "environment-1",
        "execution_id": "execution-1",
        "step_id": "step-1" if step else None,
        "action_id": "action-1" if action else None,
        "attempt": 0 if step else None,
        "source_id": "source-1",
        "source_generation": 1,
        "source_sequence": 1,
        "event_kind": kind,
        "observed_at_ms": 1,
        "payload": payload,
        "payload_sha256": event_payload_digest(kind, payload),
    }
    validator = Draft202012Validator(export_schema("runtime"))
    validator.validate(event)
    for field in ("step_id", "attempt", "action_id"):
        bad = copy.deepcopy(event)
        bad[field] = (0 if field == "attempt" else "unexpected") if event[field] is None else None
        assert not validator.is_valid(bad), (kind, field)
    bad = copy.deepcopy(event)
    bad["payload"] = {"reason_code": "owner_requested"}
    assert not validator.is_valid(bad)


def test_all_seven_records_and_seventeen_event_variants(
    action_binding, human_decision, runtime_command, all_events, receipt, outcome  # noqa: F811
):
    from lotuspond_sdk.executable.actions import build_action_intent

    validator = Draft202012Validator(export_schema("runtime"))
    records = [
        action_binding,
        build_action_intent(action_binding).model_dump(),
        human_decision,
        runtime_command,
        receipt,
        outcome,
        *all_events,
    ]
    for record in records:
        validator.validate(record)
        untagged = copy.deepcopy(record)
        del untagged["record_type"]
        assert not validator.is_valid(untagged)
        bad = copy.deepcopy(record)
        bad["format_version"] = True
        assert not validator.is_valid(bad)
    for event in all_events:
        for field in ("step_id", "action_id", "attempt"):
            bad = copy.deepcopy(event)
            bad[field] = (
                None if event[field] is not None else (0 if field == "attempt" else "unexpected")
            )
            assert not validator.is_valid(bad), (event["event_kind"], field)
        bad = copy.deepcopy(event)
        bad["payload"] = {"unrecognized": "PRIVATE_SENTINEL"}
        assert not validator.is_valid(bad)


from .test_wire import all_events, outcome, receipt  # noqa: E402,F401
