"""E05 Task 12 conformance fixtures: reviewed actions on real processes.

E04 Task 13's harness, unchanged in kind: one owned world per case; separate
killable API, orchestrator, sweeper and runtime processes; the TCP proxy; database
time; cleanup census; required mode. E05 adds only fixtures and one more owned
process, the synthetic target (`target.py`), whose store lives outside every
database cluster and backup. Write counts come only from the target's log. A harness
reviewer acts through the public task routes with a real session.
"""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from uuid import uuid4

import driver as drivers
import pytest
import world as worlds

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "actions"


def document(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


# Rules that tell a present effect from an absent one (`driver.decisive_connector`).
CONNECTOR = drivers.decisive_connector()


def reviewed_package(
    record_id,
    *,
    policy="reviewers",
    approval="human",
    timeout_seconds=600,
    expiry_seconds=3600,
    escalation=None,
    max_attempts=1,
    operation="submit",
):
    """The reviewed-action package (intent, review, dispatch, verify) for one record.

    Each case writes its own record, so a read-back answers for its own write only.
    `escalation` names the escalation group's policy document."""
    from lotuspond_sdk.executable.catalog import catalog_document_digest

    package = json.loads(json.dumps(document("reviewed_action")).replace("R-100", record_id))
    package = drivers.decisive(package, CONNECTOR)
    package["package_id"] = f"e05-{record_id.lower()}"
    # Room for read-backs on a loaded host (`driver.reviewed_action`).
    package["limits"].update(active_seconds=1000, elapsed_seconds=3600)
    steps = package["body"]["steps"]
    steps[0]["expiry_seconds"] = expiry_seconds
    steps[0]["operation"] = operation
    steps[1]["timeout_seconds"] = timeout_seconds
    steps[2]["retry"]["max_attempts"] = max_attempts
    if max_attempts > 1:
        # A command that expired unclaimed ran nowhere: the next one may take it.
        steps[2]["retry"]["retry_codes"] = ["timeout_before_dispatch"]
    if escalation is not None:
        chosen = document(escalation)
        package["policies"].append(
            {
                "id": "escalation",
                "ref": {
                    "kind": "policy",
                    "name": chosen["name"],
                    "version": chosen["version"],
                    "sha256": catalog_document_digest(chosen),
                },
            }
        )
        steps[1]["escalation_policy_ref"] = "escalation"
    if approval == "none":
        # `approval: none`: no review step; dispatch needs no decision.
        intent, dispatch, verify = steps[0], steps[2], steps[3]
        intent["approval"], intent["independent_review"] = "none", False
        dispatch.pop("decision", None)
        package["body"]["steps"] = [intent, dispatch, verify]
    if policy != "reviewers":
        chosen = document(policy)
        package["policies"][0]["ref"].update(
            name=chosen["name"], sha256=catalog_document_digest(chosen)
        )
    return package


def input_package(package_id):
    """The reviewed-action package with its target and input taken from the execution's
    input, so private values never sit in the package itself (PRIV-1)."""
    package = reviewed_package("R-input")
    package["package_id"] = package_id
    steps = package["body"]["steps"]
    target_schema = steps[0]["target"]["schema"]
    input_schema = steps[0]["input"]["schema"]
    package["input_schema"] = {
        "kind": "object",
        "fields": [
            {"name": "target", "schema": target_schema, "required": True},
            {"name": "input", "schema": input_schema, "required": True},
        ],
    }
    for step in (steps[0], steps[3]):
        for field_name in ("target", "input"):
            step[field_name] = {
                "kind": "ref",
                "source": {"kind": "package_input"},
                "pointer": f"/{field_name}",
            }
    return package


class Actions:
    """One action world's tenant, target and helpers; every call is a public route."""

    def __init__(self, world, operator, customer, target):
        self.world, self.operator, self.customer, self.target = world, operator, customer, target
        self.processes = world.processes

    # ------------------------------------------------------------ setup

    def runtime(self, name="runtime-a", **scripts):
        return self.operator.runtime(self.customer, name, **scripts)

    def environment_for(self, write, label):
        """Another environment of this tenant whose connection allows `write` instead."""
        operations = self.operator.operations
        self.operator.operations = self.operator.read_operations + (write,)
        try:
            return self.operator.environment(self.world.engine, self.customer, label=label)
        finally:
            self.operator.operations = operations

    def deploy(
        self, record_id=None, *, policy="reviewers", escalation=None, customer=None, **package
    ):
        record_id = record_id or f"R-{uuid4().hex[:8]}"
        dependencies = [CONNECTOR, document(policy)]
        if escalation is not None:
            dependencies.append(document(escalation))
        revision = self.operator.deploy(
            customer or self.customer,
            f"reviewed-{record_id}",
            package=reviewed_package(record_id, policy=policy, escalation=escalation, **package),
            dependencies=dependencies,
        )
        return revision, record_id

    def key(self, scopes):
        """An API key of the owner's, provisioned with these scopes: (raw token, id)."""
        from datetime import UTC, datetime, timedelta

        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from lotuspond.models import ApiKey, Membership, new_id
        from lotuspond.security import digest, new_token

        raw = f"lp_{new_token()}"
        with Session(self.world.engine) as db, db.begin():
            owner = db.scalar(
                select(Membership.user_id).where(
                    Membership.workspace_id == self.customer.workspace,
                    Membership.role == "owner",
                )
            )
            db.add(
                ApiKey(
                    id=new_id(),
                    workspace_id=self.customer.workspace,
                    name="conformance",
                    scopes=sorted(scopes),
                    token_hash=digest(raw),
                    prefix=raw[:11],
                    created_by_user_id=owner,
                    expires_at=datetime.now(UTC) + timedelta(days=1),
                )
            )
        return raw

    def keyed(self, raw, method, path, body=None):
        """One call with an API key alone (no session): the raw response."""
        headers = {"authorization": f"Bearer {raw}", "idempotency-key": uuid4().hex}
        return self.customer.client.request(
            method, self.customer.prefix + path, json=body, headers=headers
        )

    def release(self, person, task_id):
        return person.call("POST", f"/human-tasks/{task_id}/release", {})

    def task(self, task_id, person=None):
        return (person or self.customer).call("GET", f"/human-tasks/{task_id}")

    def start(self, revision, customer=None, value=None):
        return self.operator.start(customer or self.customer, revision, value)["id"]

    def deploy_package(self, package, label):
        return self.operator.deploy(
            self.customer, label, package=package, dependencies=[CONNECTOR, document("reviewers")]
        )

    def hand(self, name="hand-a"):
        """An enrolled runtime the test drives itself: `hand.action` is its client."""
        return self.operator.hand(self.customer, name)

    def reviewer(self, role="admin"):
        return self.operator.member(self.customer, role)

    # ------------------------------------------------------------ public routes

    def tasks(self, person=None):
        return (person or self.customer).call("GET", "/human-tasks")["items"]

    def task_of(self, execution_id, seconds=60, person=None):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            for task in self.tasks(person):
                if task["execution_id"] == execution_id:
                    return task
            time.sleep(0.2)
        raise AssertionError(drivers.diagnose(self.world, execution_id))

    def claim(self, person, task_id, expect=(200,)):
        return person.call("POST", f"/human-tasks/{task_id}/claim", {}, expect=expect)

    def decide(self, person, task_id, digest, decision="allow", decision_id=None, expect=(200,)):
        body = {
            "decision_id": decision_id or f"d-{uuid4().hex[:8]}",
            "intent_sha256": digest,
            "decision": decision,
        }
        return person.call("POST", f"/human-tasks/{task_id}/decision", body, expect=expect)

    def proposal(self, person, task_id, expect=(200,)):
        return person.call("GET", f"/human-tasks/{task_id}/proposal", expect=expect)

    def approve(self, execution_id, person=None):
        """A claim and an `allow` by an independent eligible reviewer."""
        person = person or self.reviewer()
        task = self.task_of(execution_id, person=person)
        self.claim(person, task["id"])
        return self.decide(person, task["id"], task["intent_sha256"]), person

    def actions(self, execution_id, person=None):
        return (person or self.customer).call("GET", f"/executions/{execution_id}/actions")["items"]

    def resolve(self, person, execution_id, action_id, kind, evidence=None, expect=(200,)):
        body = {"resolution_id": f"r-{uuid4().hex[:8]}", "kind": kind, "evidence": evidence}
        return person.call(
            "POST",
            f"/executions/{execution_id}/actions/{action_id}/resolution",
            body,
            expect=expect,
        )

    def execution(self, execution_id):
        return self.operator.execution(self.customer, execution_id)

    def settled(self, execution_id, states=("succeeded",), seconds=120):
        try:
            return self.operator.settled(self.customer, execution_id, states, seconds)
        except AssertionError as stalled:
            raise AssertionError((str(stalled), self.explain(execution_id))) from None

    def explain(self, execution_id):
        """Attempts, event codes, action rows and target calls, for a failure message."""
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from lotuspond.models import ExecutionActionIntent, ExecutionActionObservation

        events = [
            (row.code, payload.get("detail"), payload.get("occurrence_path"))
            for row, payload in drivers.decoded_events(self.world, execution_id)
        ]
        with Session(self.world.engine) as db:
            intents = [
                (i.authorization_state, i.effect_state, i.outcome_state)
                for i in db.scalars(
                    select(ExecutionActionIntent).where(
                        ExecutionActionIntent.execution_id == execution_id
                    )
                )
            ]
            observations = [
                (o.source, o.result, o.qualified, o.rule_id)
                for o in db.scalars(
                    select(ExecutionActionObservation).where(
                        ExecutionActionObservation.execution_id == execution_id
                    )
                )
            ]
        from lotuspond.models import ExecutionReconciliationObservation

        with Session(self.world.engine) as db:
            stops = [
                (o.source_id, o.code)
                for o in db.scalars(
                    select(ExecutionReconciliationObservation).where(
                        ExecutionReconciliationObservation.execution_id == execution_id
                    )
                )
            ]
        calls = self.target["directory"] / "calls.jsonl"
        return {
            "attempts": drivers.diagnose(self.world, execution_id),
            "events": events,
            "intents": intents,
            "observations": observations,
            "commands": [
                (c.status, c.claimed_at_ms is not None) for c in self.commands(execution_id)
            ],
            "stops": stops,
            # A runtime that could not confirm a provider child's cleanup fences itself
            # (`stop_group`): it then stops every new write `not_started`.
            # Each journaled action's phase: `claimed` alone means it stopped before its
            # provider ran; `started` that its provider ran.
            "journals": self.journals(),
            "write_calls": [
                len(path.read_text().splitlines())
                for path in (self.world.root / "runtimes").glob("*/write/provider-calls")
            ],
            "provider_failures": {
                str(path.relative_to(self.world.root / "runtimes")): path.read_text().split("\n")
                for path in (self.world.root / "runtimes").glob("*/*/provider-failures")
            },
            "fenced_runtimes": sorted(
                path.parents[1].name
                for path in (self.world.root / "runtimes").glob("*/journal/fenced")
            ),
            "target_calls": calls.read_text().splitlines()[-20:] if calls.exists() else [],
        }

    # ------------------------------------------------------------ the target

    def writes(self, record_id=None):
        """Applied writes, from the target's own log only."""
        log = self.target["directory"] / "writes.jsonl"
        rows = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        return [r for r in rows if record_id is None or r["target"]["record_id"] == record_id]

    def fault(self, *faults):
        """One-shot faults for the next writes (`target.py`)."""
        worlds.write_private(self.target["directory"] / "faults.json", list(faults))

    def journals(self):
        """(runtime, journal entry, phase) of every journaled action, read as JSON."""
        found = []
        for path in sorted((self.world.root / "runtimes").glob("*/journal/action-*.json")):
            try:
                phase = json.loads(path.read_text()).get("phase")
            except (OSError, ValueError):
                phase = "unreadable"
            found.append((path.parents[1].name, path.name[:16], phase))
        return found

    def reads_answer(self, record_id, answer):
        """Reads of this record get `answer` instead (None restores the store's)."""
        path = self.target["directory"] / "reads.json"
        current = json.loads(path.read_text()) if path.exists() else {}
        if answer is None:
            current.pop(record_id, None)
        else:
            current[record_id] = answer
        worlds.write_private(path, current)

    def commands(self, execution_id):
        from sqlalchemy import select
        from sqlalchemy.orm import Session

        from lotuspond.models import ExecutionActionCommand

        with Session(self.world.engine) as db:
            return list(
                db.scalars(
                    select(ExecutionActionCommand).where(
                        ExecutionActionCommand.execution_id == execution_id
                    )
                )
            )


def wait_until(probe, seconds=30.0, interval=0.2):
    """`probe()`'s first truthy value within `seconds`, else an assertion."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = probe()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError("condition not reached")


@pytest.fixture
def reviewed(world, processes, linux):
    """An action world: the API, one orchestrator, one sweeper, the synthetic target,
    a tenant whose connection admits the target's writes, and one runtime serving it."""
    pin = worlds.actions_mode(world, CONNECTOR)
    processes.start_api()
    processes.start_worker("orchestrator", "orchestrator-a", wait=False)
    processes.start_worker("sweeper", "sweeper-a", wait=False)
    processes.wait_ready("orchestrator-a", "sweeper-a")
    target = processes.start_target()
    destination = {"host": "localhost", "port": target["port"], "path_prefix": "/target"}
    operator = drivers.Operator(
        world,
        processes,
        connector=copy.deepcopy(CONNECTOR),
        destinations=[destination],
        actions={"pin": pin},
    )
    # Each attempt's reserved active time (E04's patient pattern). A write's lease ends
    # with its reservation, which starts when the attempt is reserved: poll, claim and
    # the records check spend it first, and on a loaded host 10 s left the provider no
    # time to start (the runtime then stops it `not_started`, safely, unsent).
    operator.demand(60000)
    customer = operator.tenant(world.engine, label="Actions")
    world.operator, world.customer, world.processes = operator, customer, processes
    helper = Actions(world, operator, customer, target)
    yield helper
    customer.close()
