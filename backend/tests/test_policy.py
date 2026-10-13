from copy import deepcopy

import pytest

from lotuspond.policy import evaluate, summarize
from lotuspond.security import canonical, digest, password_hasher, redact, verify_password


@pytest.fixture
def policy():
    return {
        "minimum_pass_rate": 1,
        "maximum_regression": 0,
        "maximum_p95_ms": 100,
        "maximum_cost_usd": 1,
        "require_approval": True,
    }


@pytest.fixture
def run():
    return {
        "id": "run",
        "project_id": "project",
        "revision": "1",
        "status": "completed",
        "scenario_snapshot": [{"id": "a", "critical": True, "input": {}, "expected": {}}],
        "results": [{"scenario_id": "a", "passed": True, "latency_ms": 30, "cost_usd": 0.05}],
    }


def test_policy_approval_boundary(policy, run):
    assert evaluate(policy, run, None, False)["verdict"] == "approval_required"
    assert evaluate(policy, run, None, True)["verdict"] == "pass"


def test_missing_duplicate_substituted_results_block(policy, run):
    for candidate in [
        {**run, "results": []},
        {**run, "status": "running"},
        {**run, "scenario_snapshot": [], "results": []},
        {**run, "results": run["results"] * 2},
        {**run, "results": [{**run["results"][0], "scenario_id": "other"}]},
    ]:
        assert evaluate(policy, candidate, None, True)["verdict"] == "block"


def test_latency_cost_critical_and_unknown_each_block(policy, run):
    for change in [{"passed": False}, {"passed": None}, {"latency_ms": 101}, {"cost_usd": 2}]:
        candidate = {**run, "results": [{**run["results"][0], **change}]}
        assert evaluate(policy, candidate, None, True)["verdict"] == "block"


def test_baselines_must_have_matching_definitions_and_projects(policy, run):
    changed = deepcopy(run)
    changed["scenario_snapshot"][0]["expected"] = {"contains": "other"}
    for baseline in [changed, {**run, "project_id": "other"}, {**run, "results": []}]:
        assert evaluate(policy, run, baseline, True)["verdict"] == "block"


def test_unavailable_cost_blocks_only_configured_cost_gate(policy, run):
    run["results"][0].update(cost_usd=0, evidence={"cost_measurement": "unavailable"})
    result = evaluate(policy, run, None, True)
    assert result["metrics"]["costUsd"] is None
    assert result["verdict"] == "block"
    assert any("cost" in reason and "unavailable" in reason for reason in result["reasons"])
    assert evaluate({**policy, "maximum_cost_usd": None}, run, None, True)["verdict"] == "pass"


def test_cost_is_unknown_without_all_required_measurements(run):
    assert summarize({**run, "results": []})["costUsd"] is None
    del run["results"][0]["cost_usd"]
    assert summarize(run)["costUsd"] is None
    run["results"][0]["cost_usd"] = 0
    assert summarize(run)["costUsd"] == 0
    run["scenario_snapshot"].append({"id": "missing", "critical": False})
    assert summarize(run)["costUsd"] is None


def test_cost_comparison_uses_decimal_sum(policy, run):
    run["scenario_snapshot"].append({"id": "b", "critical": False})
    run["results"][0]["cost_usd"] = 0.1
    run["results"].append({"scenario_id": "b", "passed": True, "latency_ms": 20, "cost_usd": 0.2})
    result = evaluate({**policy, "maximum_cost_usd": 0.3}, run, None, True)
    assert result["verdict"] == "pass"
    assert result["metrics"]["costUsd"] == 0.3


def test_security_primitives():
    assert digest(canonical({"b": [1, {"z": 2, "a": 3}], "a": 4})) == digest(
        canonical({"a": 4, "b": [1, {"a": 3, "z": 2}]})
    )
    first = password_hasher.hash("correct password")
    assert first != password_hasher.hash("correct password")
    assert verify_password("correct password", first)
    assert not verify_password("wrong password", first)
    assert redact(
        {"apiKey": "secret", "nested": [{"authorization": "Bearer secret", "count": 1}]}
    ) == {"apiKey": "[redacted]", "nested": [{"authorization": "[redacted]", "count": 1}]}


def test_token_counts_are_evidence_while_token_values_stay_redacted():
    kept = {
        # OpenTelemetry GenAI usage, as an int or as OTLP's decimal-string int64.
        "gen_ai.usage.input_tokens": "1200",
        "gen_ai.usage.output_tokens": 87,
        "total_tokens": 0,
    }
    assert redact(kept) == kept
    redacted = {
        "access_token": "eyJhbGciOi",
        "token": 42,
        "refresh_tokens": ["one", "two"],
        "id_tokens": "abc123",
        "max_tokens": True,
        "negative_tokens": -1,
        "huge_tokens": "9" * 21,
        "secret_tokens": 3,
        "api_key_tokens": "7",
    }
    assert redact(redacted) == {key: "[redacted]" for key in redacted}
