"""E06, first part: an execution's trace and its business outcome."""

import json

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from lotuspond.execution.outcomes import OutcomeProjection, business_outcome
from lotuspond.models import AuditEvent
from lotuspond.security import canonical, digest

from ..activity_fixtures import activity as activity  # noqa: F401
from ..api_fixtures import key
from ..control_fixtures import controlled as controlled  # noqa: F401
from ..test_admission import admission as admission  # noqa: F401
from ..test_budgets import budget_harness as budget_harness  # noqa: F401
from ..test_progression import flows as flows  # noqa: F401
from ..test_publication import harness as harness  # noqa: F401
from .action_api_fixtures import action_api as action_api  # noqa: F401
from .action_api_fixtures import session_for
from .dispatch_fixtures import actions as actions  # noqa: F401
from .dispatch_fixtures import intent_row
from .readback_fixtures import Target, read
from .test_action_controls import write_at
from .test_resolution import resolving as resolving  # noqa: F401
from .test_tasks import tasks as tasks  # noqa: F401
from .test_verification import pending_read, verification_package

# ---------------------------------------------------------------- the rule


@pytest.mark.parametrize(
    "state, actions, expected",
    [
        ("running", [("accepted", "unknown")], ("in_progress", False)),
        ("waiting", [], ("in_progress", False)),
        ("reconciliation_required", [("unknown", "unknown")], ("unknown", True)),
        # A write whose occurrence is uncertain, or made but never verified.
        ("failed", [("unknown", "unknown")], ("unknown", True)),
        ("succeeded", [("dispatching", "unknown")], ("unknown", True)),
        ("failed", [("observed", "unknown")], ("unknown", True)),
        (
            "failed",
            [("observed", "verified"), ("observed", "not_verified")],
            ("not_verified", True),
        ),
        ("succeeded", [("observed", "verified"), ("accepted", "verified")], ("verified", False)),
        # Succeeded with no external write: E04's meaning, not a verified effect.
        ("succeeded", [], ("completed", False)),
        ("succeeded", [("not_dispatched", "unknown"), ("absent", "unknown")], ("completed", False)),
        ("cancelled", [("observed", "verified"), ("not_dispatched", "unknown")], ("partial", True)),
        ("failed", [("observed", "verified")], ("partial", True)),
        ("cancelled", [("not_dispatched", "unknown")], ("no_effect", False)),
        ("failed", [("absent", "unknown")], ("no_effect", True)),
    ],
)
def test_the_business_outcome_never_turns_uncertainty_into_success(state, actions, expected):
    assert business_outcome(state, actions) == expected


# ---------------------------------------------------------------- the trace


def trace(h, execution_id, actor=None):
    with Session(h.engine) as db:
        return OutcomeProjection().trace(db, actor or h.actor, execution_id)


def summary(h, **options):
    with Session(h.engine) as db:
        return OutcomeProjection().summary(db, h.actor, **options)


def verified_execution(h, key_name):
    from .readback_fixtures import report

    target = Target()
    package, documents = verification_package()
    execution_id = h.reviewed(key_name, package, documents, active_ms=20000)
    delivery = h.poll()
    lease = h.claim(delivery)
    write_at(h, target, delivery)
    report(h, lease, "accepted")
    h.run2()
    return execution_id, lambda: (
        read(h, pending_read(h, execution_id).id, {"present": True}),
        h.run2(),
    )


def test_a_trace_links_the_version_the_decision_and_the_verified_outcome(actions):
    h = actions
    execution_id, verify = verified_execution(h, "traced")
    intent = intent_row(h, execution_id)
    before = trace(h, execution_id)
    [action] = before["actions"]
    # Written, not yet verified: a receipt is not an outcome.
    assert (action["effect"], action["outcome"], action["verification"]) == (
        "accepted",
        "unknown",
        None,
    )
    assert before["outcome"]["state"] in ("in_progress", "unknown")
    verify()
    after = trace(h, execution_id)
    [action] = after["actions"]
    assert after["format"] == "lotuspond.execution-trace.v1"
    assert after["execution"]["id"] == execution_id
    version = after["version"]
    assert len(version["package_sha256"]) == 64 and len(version["configuration_sha256"]) == 64
    assert action["action_id"] == intent.action_id
    assert action["intent_sha256"] == intent.intent_sha256
    assert action["operation"] and action["connector"]["name"]
    # Who allowed it: the review's decision, by a named person.
    decision = action["review"]["decision"]
    assert decision["decision"] == "allow" and decision["decided_by"]["user_id"]
    assert len(decision["decision_sha256"]) == 64
    # It happened: the dispatch, the runtime's receipt and the verified outcome record.
    assert [d["status"] for d in action["dispatches"]]
    assert [r["status"] for r in action["receipts"]] == ["accepted"]
    # Verification reads produce the outcome record; read-backs exist only for uncertain
    # writes, and this one was reported.
    assert action["readbacks"] == [] and action["verification"]["state"] == "verified"
    assert (action["effect"], action["outcome"]) == ("observed", "verified")
    # The digest covers the whole document, and nothing carries a proposal's values.
    body = {k: v for k, v in after.items() if k != "trace_sha256"}
    assert after["trace_sha256"] == digest(canonical(body))
    dumped = json.dumps(after)
    assert "R-100" not in dumped and "EUR" not in dumped
    # Reading again gives the same document.
    assert trace(h, execution_id) == after


def test_the_summary_counts_outcomes_and_exceptions(actions):
    h = actions
    execution_id, verify = verified_execution(h, "counted")
    verify()
    counted = summary(h, since_ms=0)
    assert counted["total"] >= 1 and counted["truncated"] is False
    assert sum(counted["outcomes"].values()) == counted["total"]
    assert counted["outcomes"][trace(h, execution_id)["outcome"]["state"]] >= 1
    later = trace(h, execution_id)["execution"]["admitted_at_ms"] + 1
    assert summary(h, since_ms=later)["total"] == 0


# ---------------------------------------------------------------- the routes


def get(h, path, headers):
    return h.client.get(h.M + path, headers=headers)


def test_the_trace_routes_read_as_the_execution_and_audit_downloads(action_api):
    h = action_api
    execution_id = h.reviewed("routed")
    _, viewer = session_for(h, "viewer")
    for headers in (h.owner, viewer, key(h, ["read"])):
        response = get(h, f"/executions/{execution_id}/trace", headers)
        assert response.status_code == 200, response.text
        assert response.json()["execution"]["id"] == execution_id
        assert "R-100" not in response.text and "EUR" not in response.text
    exported = get(h, f"/executions/{execution_id}/trace/export", h.owner)
    assert exported.status_code == 200
    assert "attachment" in exported.headers["content-disposition"]
    assert exported.headers["cache-control"] == "no-store"
    with Session(h.engine) as db:
        audits = db.scalars(
            select(AuditEvent).where(AuditEvent.action == "executions.trace_exported")
        ).all()
    assert [event.resource_id for event in audits] == [execution_id]
    missing = "00000000-0000-4000-8000-000000000000"
    assert get(h, f"/executions/{missing}/trace", h.owner).status_code == 404
    assert get(h, f"/executions/{execution_id}/trace?x=1", h.owner).status_code == 400
    other = h.M.replace(h.scope.environment_id, missing)
    assert (
        h.client.get(other + f"/executions/{execution_id}/trace", headers=h.owner).status_code
        == 404
    )


def test_the_outcome_counts_route_checks_its_query(action_api):
    h = action_api
    h.reviewed("summarized")
    response = get(h, "/outcomes?since_ms=0", h.owner)
    assert response.status_code == 200, response.text
    assert response.json()["total"] >= 1
    for query in (
        "",
        "?since_ms=-1",
        "?since_ms=0&since_ms=1",
        "?since_ms=0&state=x",
        "?since_ms=0&deployment_id=x",
    ):
        assert get(h, f"/outcomes{query}", h.owner).status_code == 400, query


def test_the_trace_needs_no_action_services(action_api):
    """A server without a command key still traces its executions (v1 ones have no
    actions), where the separate action routes answer 503."""
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from lotuspond.app import create_app

    from ..api_fixtures import api_settings

    h = action_api
    execution_id = h.reviewed("unwired")
    services = replace(h.services, actions=None)
    application = create_app(
        api_settings(), engine=h.engine, fleet_dependencies=h.fleet, execution_services=services
    )
    with TestClient(application, headers={"origin": "http://testserver"}) as client:
        traced = client.get(h.M + f"/executions/{execution_id}/trace", headers=h.owner)
        assert traced.status_code == 200, traced.text
        counted = client.get(h.M + "/outcomes?since_ms=0", headers=h.owner)
        assert counted.status_code == 200, counted.text
