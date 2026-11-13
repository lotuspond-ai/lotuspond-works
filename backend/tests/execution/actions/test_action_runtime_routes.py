"""E05 Task 11: the runtime action routes, exercised through the runtime's own client.

Writes are counted at the fake target; commands as rows the API admitted.
"""

import json
from hashlib import sha256

import pytest
from lotuspond_runtime.action_protocol import ActionReport, ActionStop
from lotuspond_runtime.errors import FleetError

from ..activity_fixtures import activity as activity  # noqa: F401
from ..api_fixtures import key
from ..control_fixtures import controlled as controlled  # noqa: F401
from ..test_admission import admission as admission  # noqa: F401
from ..test_budgets import budget_harness as budget_harness  # noqa: F401
from ..test_progression import flows as flows  # noqa: F401
from ..test_publication import harness as harness  # noqa: F401
from .action_api_fixtures import action_api as action_api  # noqa: F401
from .action_api_fixtures import action_client, runtime_headers
from .dispatch_fixtures import action_runtime, intent_row
from .dispatch_fixtures import actions as actions  # noqa: F401
from .readback_fixtures import Target, commands
from .test_resolution import resolving as resolving  # noqa: F401
from .test_tasks import tasks as tasks  # noqa: F401

ROUTES = (
    "/actions/poll",
    "/actions/records",
    "/actions/claim",
    "/actions/renew",
    "/actions/receipts",
    "/actions/stops",
    "/actions/00000000-0000-4000-8000-000000000000/values",
)
USAGE = {"input_tokens": 0, "output_tokens": 0, "cost_microusd": 0, "completed_at_ms": 0}


def raw(h, route, body, headers):
    return h.client.post("/api/v1/runtime" + route, content=body, headers=headers)


def report_of(h, execution_id, lease, status="accepted", response=None):
    intent = intent_row(h, execution_id)
    return ActionReport.model_validate(
        {
            "attempt_id": lease.attempt_id,
            "lease_revision": lease.lease_revision,
            "receipt": {
                "receipt_id": lease.attempt_id,
                "action_id": intent.action_id,
                "intent_sha256": intent.intent_sha256,
                "attempt": lease.ordinal,
                "target_operation_ref": "submit",
                "status": status,
                "evidence_refs": [],
            },
            "response": response,
            "usage": USAGE,
        }
    )


def code_of(call):
    with pytest.raises(FleetError) as refused:
        call()
    return refused.value.code


def test_a_runtime_writes_once_through_the_routes(action_api):
    h = action_api
    target = Target()
    execution_id = h.reviewed("wired", active_ms=60000)
    client = action_client(h)
    delivery = client.poll()
    assert delivery is not None
    lease = client.claim(delivery)
    assert client.claim(delivery) == lease  # an equal claim returns the same live lease
    records = client.records(delivery)
    target.write(
        records.intent.binding.action_id,
        json.loads(records.actual_target),
        json.loads(records.actual_input),
    )
    renewed = client.renew(lease)
    assert renewed.attempt_id == lease.attempt_id
    handle = client.store(lease.attempt_id, b'{"accepted":true}')
    client.receipt(report_of(h, execution_id, renewed, response=handle.model_dump(mode="json")))
    assert intent_row(h, execution_id).effect_state == "accepted"
    assert client.poll() is None
    assert target.writes == 1 and len(commands(h, execution_id)) == 1


def test_runtime_action_routes_are_session_bound(action_api):
    h = action_api
    execution_id = h.reviewed("bound", active_ms=60000)
    client = action_client(h)
    delivery = client.poll()
    # Another runtime session of the pool never sees, claims or reports this command.
    other = action_runtime(h, h.pin)
    other_client = action_client(h, runtime_headers(h, other))
    assert other_client.poll() is None
    assert code_of(lambda: other_client.claim(delivery)) in {
        "unauthenticated",
        "forbidden",
        "not_found",
        "conflict",
    }
    lease = client.claim(delivery)
    other_wire = runtime_headers(h, other)
    other_session_receipt_response = raw(
        h,
        "/actions/receipts",
        json.dumps(report_of(h, execution_id, lease).model_dump(mode="json")).encode(),
        {**other_wire, "content-type": "application/json"},
    )
    # Its live lease is another session's: refused, never recorded.
    assert other_session_receipt_response.status_code == 409
    stop = ActionStop.model_validate(
        {
            "attempt_id": lease.attempt_id,
            "lease_revision": lease.lease_revision,
            "code": "not_started",
            "usage": USAGE,
        }
    )
    other_stop = raw(
        h,
        "/actions/stops",
        json.dumps(stop.model_dump(mode="json")).encode(),
        {**other_wire, "content-type": "application/json"},
    )
    assert other_stop.status_code == 409
    assert intent_row(h, execution_id).effect_state == "dispatching"
    # Without its session, or with customer credentials, no action route answers.
    token_only = {"authorization": h.wire["authorization"], "content-type": "application/json"}
    for headers in (token_only, {**h.owner, "content-type": "application/json"}):
        for route in ROUTES:
            assert raw(h, route, b"{}", headers).status_code == 401, (route, headers)
    every = {**key(h, ["execute", "read", "write"]), "content-type": "application/json"}
    assert raw(h, "/actions/poll", b"{}", every).status_code == 401
    # And runtime credentials never enter the customer task routes.
    assert h.client.get(h.M + "/human-tasks", headers=h.wire).status_code == 401
    # E03's generic command claim refuses an action command.
    command = json.loads(delivery.command_bytes)
    generic = raw(
        h,
        f"/commands/{command['command_id']}/claim",
        json.dumps({"command_sha256": sha256(delivery.command_bytes).hexdigest()}).encode(),
        {**h.wire, "content-type": "application/json"},
    )
    assert generic.status_code != 200
    client.stop(stop)  # its own session may stop it
    assert intent_row(h, execution_id).effect_state != "accepted"


def test_boundary_and_body_limits(action_api, monkeypatch):
    from lotuspond.execution import action_runtime_routes

    h = action_api
    h.reviewed("bounded", active_ms=60000)
    wire = {**h.wire, "content-type": "application/json"}
    # Runtime bodies are bounded at 64 KiB, before decoding.
    oversized = b'{"pad":"' + b"x" * 65536 + b'"}'
    assert raw(h, "/actions/receipts", oversized, wire).status_code == 413
    # Duplicated authority headers are refused, on both APIs.
    duplicated = [
        ("authorization", h.wire["authorization"]),
        ("authorization", h.wire["authorization"]),
        ("x-lotuspond-runtime-session", h.wire["x-lotuspond-runtime-session"]),
        ("content-type", "application/json"),
    ]
    response = h.client.post("/api/v1/runtime/actions/poll", content=b"{}", headers=duplicated)
    assert response.status_code in {400, 401}
    sessions = [
        ("authorization", h.wire["authorization"]),
        ("x-lotuspond-runtime-session", h.wire["x-lotuspond-runtime-session"]),
        ("x-lotuspond-runtime-session", h.wire["x-lotuspond-runtime-session"]),
        ("content-type", "application/json"),
    ]
    response = h.client.post("/api/v1/runtime/actions/poll", content=b"{}", headers=sessions)
    assert response.status_code in {400, 401}
    cookies = [("cookie", h.owner["cookie"])] * 2 + [
        ("x-lotuspond-workspace", h.owner["x-lotuspond-workspace"])
    ]
    assert h.client.get(h.M + "/human-tasks", headers=cookies).status_code == 400
    # Customer bodies are bounded at 1 MiB.
    large = json.dumps({"submission_id": "s", "payload": "x" * 1048576}).encode()
    assert (
        h.client.post(
            h.M + "/human-tasks/00000000-0000-4000-8000-000000000000/submission",
            content=large,
            headers={**h.owner, "content-type": "application/json"},
        ).status_code
        == 413
    )
    # A delivery is bounded at 131,072 bytes: past it, a closed refusal.
    monkeypatch.setattr(action_runtime_routes, "MAX_DELIVERY_BYTES", 16)
    response = raw(h, "/actions/poll", b"{}", wire)
    assert (response.status_code, response.json()["error"]) == (413, "input_limit")
    # A body of the wrong shape is a closed 400.
    response = raw(h, "/actions/claim", b'{"command_id":1}', wire)
    assert (response.status_code, response.json()["error"]) == (400, "invalid_request")


def test_an_ended_session_is_refused_with_nothing_to_hand_out(action_api):
    """With no work at all, a poll still tells an expired or revoked session it ended."""
    from sqlalchemy.orm import Session

    from lotuspond.fleet.authority import database_now_ms
    from lotuspond.models import RuntimeCredential, RuntimeSession

    h = action_api
    live = {**h.wire, "content-type": "application/json"}
    assert raw(h, "/actions/poll", b"{}", live).content == b"null"  # live, nothing to do
    for model, field, ended in (
        (RuntimeSession, "expires_at_ms", None),
        (RuntimeSession, "status", "revoked"),
        (RuntimeCredential, "expires_at_ms", None),
    ):
        other = action_runtime(h, h.pin)
        wire = {**runtime_headers(h, other), "content-type": "application/json"}
        assert raw(h, "/actions/poll", b"{}", wire).content == b"null"
        row_id = other.session_id if model is RuntimeSession else other.credential_id
        with Session(h.engine) as db, db.begin():
            value = database_now_ms(db) - 1 if ended is None else ended
            setattr(db.get(model, row_id), field, value)
        response = raw(h, "/actions/poll", b"{}", wire)
        assert (response.status_code, response.json()["error"]) == (401, "unauthenticated")
