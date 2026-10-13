from decimal import Decimal
from math import ceil

from .security import canonical


def measured_cost(run: dict) -> Decimal | None:
    results = run["results"]
    expected = {row["id"] for row in run["scenario_snapshot"]}
    if (
        not results
        or len(results) != len(expected)
        or {row["scenario_id"] for row in results} != expected
    ):
        return None
    costs = []
    for result in results:
        # Older explicit-cost fixtures predate provenance; an unavailable marker
        # always wins over the storage placeholder or an adapter's numeric value.
        marker = result.get("evidence", {}).get("cost_measurement")
        if result.get("cost_usd") is None or marker not in {None, "reported"}:
            return None
        cost = Decimal(str(result["cost_usd"]))
        if not cost.is_finite() or cost < 0:
            return None
        costs.append(cost)
    return sum(costs, Decimal(0))


def summarize(run: dict) -> dict:
    results = run["results"]
    count = len(run["scenario_snapshot"])
    latencies = sorted(result["latency_ms"] for result in results)
    cost = measured_cost(run)
    return {
        "total": count,
        "received": len(results),
        "unknown": sum(result["passed"] is None for result in results),
        "passRate": sum(result["passed"] is True for result in results) / count if count else 0,
        "p95Ms": latencies[ceil(len(latencies) * 0.95) - 1] if latencies else None,
        "costUsd": float(cost) if cost is not None else None,
    }


def complete(run: dict) -> bool:
    expected = {row["id"] for row in run["scenario_snapshot"]}
    actual = {row["scenario_id"] for row in run["results"]}
    return (
        run["status"] == "completed"
        and bool(expected)
        and expected == actual
        and len(actual) == len(run["results"])
        and all(row["passed"] is not None for row in run["results"])
    )


def evaluate(policy: dict, candidate: dict, baseline: dict | None, approved: bool) -> dict:
    reasons = []
    metrics = summarize(candidate)
    if not complete(candidate):
        reasons.append("Candidate run lacks a conclusive result for every scenario.")
    if metrics["passRate"] < float(policy["minimum_pass_rate"]):
        reasons.append("Pass rate is below the policy minimum.")
    results = {row["scenario_id"]: row for row in candidate["results"]}
    if any(
        row["critical"] and results.get(row["id"], {}).get("passed") is not True
        for row in candidate["scenario_snapshot"]
    ):
        reasons.append("A critical scenario failed or has no conclusive result.")
    if policy["maximum_p95_ms"] is not None and (
        metrics["p95Ms"] is None or metrics["p95Ms"] > policy["maximum_p95_ms"]
    ):
        reasons.append("P95 latency exceeds the policy limit or is unavailable.")
    if policy["maximum_cost_usd"] is not None:
        cost = measured_cost(candidate)
        if cost is None or cost > Decimal(str(policy["maximum_cost_usd"])):
            reasons.append("Evaluation cost exceeds the policy limit or is unavailable.")
    if baseline:
        if not complete(baseline):
            reasons.append("Baseline run lacks complete conclusive evidence.")

        def signature(run):
            return canonical(sorted(run["scenario_snapshot"], key=lambda row: row["id"]))

        if baseline["project_id"] != candidate["project_id"] or signature(baseline) != signature(
            candidate
        ):
            reasons.append(
                "Candidate and baseline must evaluate the same project and scenario snapshot."
            )
        if (
            summarize(baseline)["passRate"] - metrics["passRate"]
            > float(policy["maximum_regression"]) + 1e-12
        ):
            reasons.append("Candidate regresses beyond the permitted baseline threshold.")
    verdict = (
        "block"
        if reasons
        else "approval_required"
        if policy["require_approval"] and not approved
        else "pass"
    )
    if verdict == "approval_required":
        reasons.append("An independent administrator must approve this release.")
    if verdict == "pass":
        reasons.append("All configured release checks passed.")
    return {
        "verdict": verdict,
        "reasons": reasons,
        "metrics": metrics,
        "baselineMetrics": summarize(baseline) if baseline else None,
    }
