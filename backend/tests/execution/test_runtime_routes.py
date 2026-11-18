"""Task 12: the runtime activity routes, exercised through Task 10's own client.

`HttpTransport` stands in for the runtime's TLS control transport and forwards
each call to the API, so the runtime's real request building and response checks
meet the real routes.
"""

import copy
import json
from hashlib import sha256

import pytest
from lotuspond_runtime.activity_client import ActivityClient, canonical_value
from lotuspond_runtime.activity_protocol import ActivityResult, ChildActivityRequest
from lotuspond_runtime.context import ContextRef
from lotuspond_runtime.control_transport import _STATUS
from lotuspond_runtime.errors import FleetError
from lotuspond_sdk.activity import UsageObservation, validate_activity
from lotuspond_sdk.executable.catalog import catalog_document_digest
from lotuspond_sdk.executable.input import decode_executable_bytes
from pydantic import SecretStr
from sqlalchemy.orm import Session

from lotuspond.execution.authorization import authorize_execution
from lotuspond.execution.contracts import DependencyBinding
from lotuspond.execution.deployments import create_deployment, set_deployment_status
from lotuspond.fleet.authority import database_now_ms
from lotuspond.models import ExecutionActivityDelivery, ExecutionAttempt, new_id

from .activity_fixtures import FLOW, documents
from .activity_fixtures import activity as activity
from .api_fixtures import api as api
from .control_fixtures import controlled as controlled
from .test_admission import admission as admission
from .test_budgets import budget_harness as budget_harness
from .test_deployments import connection_request
from .test_progression import flows as flows
from .test_publication import harness as harness


class HttpTransport:
    """The runtime's control transport, over the API test client instead of TLS."""

    def __init__(self, client):
        self.client = client

    def request(self, method, route, body, token, session):
        headers = {
            "authorization": "Bearer " + token.get_secret_value(),
            "content-type": "application/json",
        }
        if session is not None:
            headers["x-lotuspond-runtime-session"] = session.get_secret_value()
        response = self.client.request(
            method, "/api/v1/runtime" + route, content=body, headers=headers
        )
        if response.status_code != 200:
            raise FleetError(_STATUS.get(response.status_code, "transport_failed"))
        return response.content

    def close(self):
        pass


# A test that holds an attempt across several runtime round trips uses `activity_slow`:
# `activity_read`'s read times out after 2 s, and the lease never outlives the attempt,
# so a slow runner saw it lapse mid-test (`conflict` instead of the asserted refusal).
# As in test_activity_races.py, the reservation covers one lease's maximum.
def held_read(h):
    return h.ready("activity_slow", active_ms=60000)


def runtime_client(h):
    token = h.runtime["authorization"].removeprefix("Bearer ")
    session = h.runtime["x-lotuspond-runtime-session"]
    return ActivityClient(HttpTransport(h.client), SecretStr(token), SecretStr(session))


def envelope_of(delivery):
    return validate_activity(decode_executable_bytes(delivery.envelope.encode("utf-8")))


def usage(h, **values):
    with Session(h.engine) as db:
        now = database_now_ms(db)
    return UsageObservation(
        **{"input_tokens": 0, "output_tokens": 0, "cost_microusd": 0, **values},
        completed_at_ms=now - 1,
    )


def code_of(call):
    with pytest.raises(FleetError) as refused:
        call()
    return refused.value.code


def test_a_runtime_runs_a_read_through_the_routes(api):
    h = api
    execution_id, attempt_id = held_read(h)
    client = runtime_client(h)
    delivery = client.poll()
    envelope = envelope_of(delivery)
    assert envelope.attempt_id == attempt_id
    # The pinned records of its own delivery, rebuilt by the control plane.
    records = client.records(envelope)
    assert records.envelope == envelope and records.dependency == envelope.dependency
    assert records.request_sha256 == envelope.input.sha256 and records.connection is not None
    lease = client.claim(delivery)
    # Its input, and nothing else: the exact bytes the envelope signed.
    content = client.input(attempt_id, envelope.input)
    assert sha256(content).hexdigest() == records.request_sha256
    handle = client.store(attempt_id, canonical_value(None))
    # Another artifact, even one this very attempt stored, is never served as input.
    assert code_of(lambda: client.input(attempt_id, handle)) == "forbidden"
    lease = client.renew(lease)
    client.result(
        ActivityResult(
            attempt_id=attempt_id,
            lease_revision=lease.lease_revision,
            result=handle,
            code="succeeded",
            usage=usage(h),
        )
    )
    h.run()
    detail = h.client.get(h.M + f"/executions/{execution_id}", headers=h.owner).json()
    assert detail["state"] == "succeeded"


def test_refusals_tell_a_bad_delivery_from_an_outage(api):
    h = api
    _, attempt_id = held_read(h)
    client = runtime_client(h)
    delivery = client.poll()
    envelope = envelope_of(delivery)
    # A delivery this runtime will not be served: refused with a declinable code.
    unknown = envelope.model_copy(update={"delivery_id": new_id()})
    assert code_of(lambda: client.records(unknown)) == "not_found"
    altered = envelope.model_copy(update={"replay_id": new_id()})
    assert code_of(lambda: client.records(altered)) == "conflict"
    # Its input before any claim: never served.
    assert code_of(lambda: client.input(attempt_id, envelope.input)) == "conflict"
    # An outage is not the delivery's fault: a transient code, never declinable.
    h.fleet_recovery.available = False
    assert code_of(lambda: client.records(envelope)) == "authority_unavailable"
    h.fleet_recovery.available = True
    # Declined: the attempt fails as if it had expired, and the delivery is closed.
    client.decline(delivery)
    assert code_of(lambda: client.records(envelope)) == "conflict"
    with Session(h.engine) as db:
        attempt = db.get(ExecutionAttempt, attempt_id)
        stored = db.get(ExecutionActivityDelivery, envelope.delivery_id)
        assert (attempt.state, stored.status) == ("failed", "conflict")


def test_an_agent_reads_its_declarations_and_requests_children(api):
    h = api
    model = json.loads((FLOW / "agent_tools.model.json").read_text())
    _, parent = h.ready("agent_tools", model=model, path="think")
    client = runtime_client(h)
    delivery = client.poll()
    envelope = envelope_of(delivery)
    # Declarations belong to a claimed root: not before the claim.
    assert code_of(lambda: client.agent(envelope)) == "conflict"
    client.claim(delivery)
    agent = client.agent(envelope)
    assert agent.model == envelope.dependency and agent.model_name == model["name"]
    assert agent.instructions == "Synthetic typed input only" and agent.context == ()
    assert [tool.operation for tool in agent.tools] == ["read"]
    assert (agent.max_steps, agent.max_input_tokens, agent.max_output_tokens) == (10, 1000, 1000)
    assert agent.max_cost_microusd == 100000
    arguments = client.store(parent, canonical_value({"target": None, "input": None}))
    child_id = client.request_child(
        ChildActivityRequest(
            request_id="read-1",
            parent_attempt_id=parent,
            kind="connector_read",
            declaration_name=agent.tools[0].name,
            arguments=arguments,
        )
    )
    child = envelope_of(client.poll(parent_attempt_id=parent))
    assert child.attempt_id == child_id and child.input == arguments
    # A child is no agent: it has no declarations of its own.
    assert code_of(lambda: client.agent(child)) in ("conflict", "forbidden")


def deploy_with_knowledge(h, content):
    """The agent fixture, with one pinned knowledge source bound at deployment."""
    connector, model = documents()[0], json.loads((FLOW / "agent_tools.model.json").read_text())
    knowledge = {
        "format_version": 1,
        "kind": "knowledge",
        "name": "knowledge-route",
        "version": "1.0.0",
        "schemas": [],
        "definition": {
            "source_revision": "1",
            "content_sha256": sha256(content).hexdigest(),
            "freshness_seconds": 3600,
            "classification": "synthetic",
            "citation_schema": {"kind": "null"},
        },
    }
    reference = {
        "kind": "knowledge",
        "name": knowledge["name"],
        "version": knowledge["version"],
        "sha256": catalog_document_digest(knowledge),
    }
    package = copy.deepcopy(json.loads((FLOW / "agent_tools.json").read_text()))
    package["package_id"] = "fixture-agent-knowledge"
    package["knowledge"] = [
        {
            "id": "facts",
            "ref": reference,
            "required_freshness_seconds": 3600,
            "allowed_classifications": ["synthetic"],
            "citation_schema": {"kind": "null"},
        }
    ]
    package["body"]["steps"][0]["knowledge_refs"] = ["facts"]
    source_id = new_id()
    with Session(h.engine) as db, db.begin():
        now = database_now_ms(db)
        manager = authorize_execution(db, h.principal, h.scope, "manage")
        h.context.register(
            db,
            manager,
            source_id=source_id,
            version=1,
            content=content,
            citation=b"null",
            classification="synthetic",
            observed_at_ms=now,
            expires_at_ms=now + 3_600_000,
            retain_until_ms=now + 3_600_000,
            access_policy_id="fixture-access",
            key_id="fixture",
        )
        ids = []
        for document, request_key in ((connector, "k-c"), (model, "k-m"), (knowledge, "k-k")):
            ids.append(
                h.publish(
                    db,
                    document,
                    tuple(ids[:1]) if document is model else (),
                    request_key=request_key,
                ).id
            )
        publication = h.publish(db, package, tuple(ids), request_key="k-p")
        request = connection_request(db, h, publication, [connector, model, knowledge])
        bindings = tuple(
            DependencyBinding(dependency=reference, source_id=source_id, source_version=1)
            if binding.dependency.model_dump() == reference
            else binding
            for binding in request.configuration.bindings
        )
        request = request.model_copy(
            update={
                "configuration": request.configuration.model_copy(update={"bindings": bindings})
            }
        )
        view = create_deployment(db, manager, request, "knowledge", dependencies=h.dependencies)
        set_deployment_status(
            db, manager, view.id, view.revision_id, "enabled", "k-e", dependencies=h.dependencies
        )
    h.deployed["agent_knowledge"] = view.revision_id
    return source_id


def test_an_agent_reads_pinned_context_under_current_access(api):
    h = api
    content = b'{"fact":"synthetic context"}'
    source_id = deploy_with_knowledge(h, content)
    _, parent = h.ready("agent_knowledge", path="think")
    client = runtime_client(h)
    delivery = client.poll()
    envelope = envelope_of(delivery)
    client.claim(delivery)
    agent = client.agent(envelope)
    assert agent.context == (ContextRef(source_id, 1),)
    assert client.context(parent, ContextRef(source_id, 1)) == {"fact": "synthetic context"}
    # Only what the step pinned, and only under current source access.
    assert code_of(lambda: client.context(parent, ContextRef(new_id(), 1))) == "forbidden"
    assert code_of(lambda: client.context(parent, ContextRef(source_id, 2))) == "forbidden"
    h.access.allowed = False
    assert code_of(lambda: client.context(parent, ContextRef(source_id, 1))) == "forbidden"
    h.access.allowed = True
    response = h.client.post(
        h.M + f"/context-sources/{source_id}/revoke",
        json={},
        headers={**h.owner, "idempotency-key": "revoke"},
    )
    assert response.status_code == 200, response.text
    # A revoked source withdraws the deployment pinning it (Task 3's check), so the
    # read is refused before any source access, as an unavailable dependency.
    assert code_of(lambda: client.context(parent, ContextRef(source_id, 1))) == (
        "authority_unavailable"
    )


def test_runtime_routes_need_the_runtime_and_its_session(api):
    h = api
    for headers in ({}, {"authorization": h.runtime["authorization"]}, h.owner):
        for route in (
            "/activities/poll",
            "/activities/claim",
            "/activity-artifacts/records",
            "/activity-artifacts/agent",
        ):
            response = h.client.post("/api/v1/runtime" + route, json={}, headers=headers)
            assert response.status_code == 401, (route, headers)


def test_an_oversize_delivery_is_a_closed_refusal(api, monkeypatch):
    from lotuspond.execution import runtime_routes

    h = api
    h.ready("activity_read")
    monkeypatch.setattr(runtime_routes, "MAX_DELIVERY_BYTES", 16)
    response = h.client.post("/api/v1/runtime/activities/poll", json={}, headers=h.runtime)
    assert (response.status_code, response.json()["error"]) == (413, "input_limit")
