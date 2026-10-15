"""Fresh, synthetic source documents shared with compiler tests."""

import copy
import json
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parents[4] / "contracts/examples/executable-v2"


@pytest.fixture
def case():
    def load(name: str) -> dict:
        return copy.deepcopy(json.loads((EXAMPLES / f"{name}.json").read_text()))

    return load


@pytest.fixture
def catalog_case(case):
    return lambda: case("catalog")


@pytest.fixture
def v1_example(example):
    return example


@pytest.fixture
def action_binding():
    return {
        "record_type": "action_binding",
        "format_version": 1,
        "installation_id": "installation-1",
        "recovery_epoch": 2,
        "workspace_id": "workspace-1",
        "project_id": "project-1",
        "environment_id": "environment-1",
        "execution_id": "execution-1",
        "step_id": "dispatch",
        "action_id": "action-1",
        "package_sha256": "1" * 64,
        "configuration_sha256": "2" * 64,
        "plan_sha256": "3" * 64,
        "connector_name": "synthetic",
        "connector_version": "1.0.0",
        "connector_artifact_sha256": "4" * 64,
        "operation": "submit",
        "target_sha256": "5" * 64,
        "input_sha256": "6" * 64,
        "policy_sha256": "7" * 64,
        "approval_mode": "human",
        "independent_approval": True,
        "requester_id": "requester-1",
        "issued_at_ms": 1000,
        "expires_at_ms": 10000,
    }


@pytest.fixture
def binding_context(action_binding):
    names = (
        "installation_id",
        "recovery_epoch",
        "workspace_id",
        "project_id",
        "environment_id",
        "execution_id",
        "step_id",
        "action_id",
        "package_sha256",
        "configuration_sha256",
        "plan_sha256",
        "policy_sha256",
        "requester_id",
    )
    return {
        **{k: action_binding[k] for k in names},
        "worker_id": "worker-1",
        "fencing_generation": 3,
        "now_ms": 2000,
    }


@pytest.fixture
def human_decision(action_binding):
    from lotuspond_sdk.executable.actions import build_action_intent

    return {
        "record_type": "human_decision",
        "format_version": 1,
        "decision_id": "decision-1",
        "intent_sha256": build_action_intent(action_binding).sha256,
        "decision": "allow",
        "actor_id": "reviewer-1",
        "actor_kind": "human",
        "policy_sha256": action_binding["policy_sha256"],
        "issued_at_ms": 1500,
        "expires_at_ms": 9000,
    }


@pytest.fixture
def runtime_command(action_binding, human_decision, binding_context):
    from lotuspond_sdk.executable.actions import build_action_intent, decision_digest

    names = (
        "installation_id",
        "recovery_epoch",
        "workspace_id",
        "project_id",
        "environment_id",
        "execution_id",
        "action_id",
        "worker_id",
        "fencing_generation",
    )
    return {
        "record_type": "runtime_command",
        "format_version": 1,
        **{k: binding_context[k] for k in names},
        "command_id": "command-1",
        "replay_id": "replay-1",
        "intent_sha256": build_action_intent(action_binding).sha256,
        "decision_sha256": decision_digest(human_decision),
        "issued_at_ms": 1800,
        "expires_at_ms": 8000,
    }


@pytest.fixture
def evidence_ref():
    return {
        "artifact_id": "artifact-1",
        "sha256": "8" * 64,
        "media_type": "application/json",
        "classification": "internal",
        "source_kind": "target_readback",
    }
