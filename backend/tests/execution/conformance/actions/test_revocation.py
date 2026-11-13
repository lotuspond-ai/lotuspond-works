"""E05 Task 12: revocation around reviewed writes on real processes (tier 1).

REVA-1 to REVA-5 and spike check 35. Operator-side authority changes are direct row
or authority-file edits, as an operator's tools make them; runtime revocation uses its
public route. A positive control precedes each denial.
"""

from __future__ import annotations

import signal
import time

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from lotuspond.models import (
    Connection,
    ExecutionActionIntent,
    ExecutionArtifact,
    Membership,
    RuntimeCommandAdmission,
    RuntimePoolPolicyConnection,
)

from .conftest import wait_until


def set_connection(a, status):
    with Session(a.world.engine) as db, db.begin():
        db.execute(
            update(Connection)
            .where(Connection.id == a.customer.connection["id"])
            .values(status=status)
        )


def test_reva_1_a_connection_revoked_after_allow_sends_no_command(reviewed):
    a = reviewed
    runtime = a.runtime("runtime-a")
    revision, record = a.deploy()
    control = a.start(revision)  # positive control on the same connection first
    a.approve(control)
    a.settled(control)
    assert len(a.writes(record)) == 1
    revision, record = a.deploy()
    execution_id = a.start(revision)
    # The runtime is held still while the approval and then the revocation commit.
    runtime["process"].send_signal(signal.SIGSTOP)
    try:
        a.approve(execution_id)
        set_connection(a, "revoked")
    finally:
        runtime["process"].send_signal(signal.SIGCONT)
    try:
        # No runtime can authenticate against the revoked connection's pool: the
        # approved write waits, reserved, and no command is ever admitted.
        wait_until(lambda: a.execution(execution_id).get("reason") == "authority_revoked", 60)
        time.sleep(15)
        assert a.commands(execution_id) == [] and a.writes(record) == []
        a.operator.control(a.customer, execution_id, "cancel")  # before authority returns
        a.settled(execution_id, ("cancelled",), seconds=60)
    finally:
        set_connection(a, "active")
    assert a.commands(execution_id) == [] and a.writes(record) == []


def test_reva_2_a_runtime_revoked_while_holding_a_lease(reviewed):
    """Renewal and receipt refused; the lease lapses; another runtime's read-back
    settles the effect; one write."""
    a = reviewed
    first = a.runtime("runtime-a")
    revision, record = a.deploy()
    execution_id = a.start(revision)
    a.fault("hang_after_apply")
    a.approve(execution_id)
    wait_until(lambda: a.writes(record), 60)
    a.operator.revoke(a.customer, first["instance_id"])
    assert first["process"].wait(timeout=90) == 3  # the revoked runtime stops
    a.runtime("runtime-b")
    wait_until(lambda: a.actions(execution_id)[0]["effect"] == "observed", 240)
    # A revoked runtime can never report its stop: an owner confirms it (the runbook),
    # which frees the write's slot so verification runs.
    [action] = a.actions(execution_id)
    a.resolve(a.customer, execution_id, action["action_id"], "compute_stopped")
    a.settled(execution_id, seconds=240)
    [action] = a.actions(execution_id)
    assert action["effect"] == "observed" and action["receipt_status"] != "accepted"
    assert len(a.writes(record)) == 1 and len(a.commands(execution_id)) == 1


def test_reva_3_the_pool_dropping_the_connector_refuses_the_claim(reviewed):
    a = reviewed
    hand = a.hand()
    try:
        revision, record = a.deploy()
        execution_id = a.start(revision)
        a.approve(execution_id)
        delivery = wait_until(hand.action.poll, 60)
        with Session(a.world.engine) as db, db.begin():
            db.execute(delete(RuntimePoolPolicyConnection))
        from lotuspond_runtime.errors import FleetError

        try:
            hand.action.claim(delivery)
            raise AssertionError("claimed after the pool dropped the connector")
        except FleetError as refused:
            # A policy without the connection cannot authorize it, now or later.
            assert refused.code in (
                "forbidden",
                "unauthenticated",
                "conflict",
                "not_found",
                "authority_unavailable",
            )
        [command] = a.commands(execution_id)
        with Session(a.world.engine) as db, db.begin():
            db.execute(
                update(RuntimeCommandAdmission)
                .where(RuntimeCommandAdmission.id == command.admission_id)
                .values(command_expires_at_ms=1)
            )
        wait_until(lambda: a.commands(execution_id)[0].status == "expired", 60)
        assert a.writes(record) == []
    finally:
        hand.close()


def test_reva_4_an_expired_entitlement_stops_dispatch_not_reads(reviewed):
    a = reviewed
    revision, record = a.deploy()
    execution_id = a.start(revision)
    a.approve(execution_id)
    entitlement = a.world.state("entitlement")
    a.world.update("entitlement", expires_at_ms=0, price_expires_at_ms=0)
    try:
        a.runtime("runtime-a")
        time.sleep(8)
        assert a.commands(execution_id) == [] and a.writes(record) == []
        # Reads and exports of evidence still answer.
        assert a.actions(execution_id)[0]["authorization"] == "approved"
        assert a.operator.events(a.customer, execution_id)
    finally:
        a.world.update(
            "entitlement",
            expires_at_ms=entitlement["expires_at_ms"],
            price_expires_at_ms=entitlement["price_expires_at_ms"],
        )
    # Positive control: with the authority current again, the same dispatch proceeds.
    final = a.settled(execution_id, ("succeeded", "failed"), seconds=180)
    if final["state"] == "succeeded":
        assert len(a.writes(record)) == 1
    else:  # its unclaimed attempt timed out meanwhile: still nothing written
        assert a.writes(record) == []


def test_reva_5_an_initiator_losing_execute_stops_new_dispatch_only(reviewed):
    """In-flight writes finish and record; nothing new is dispatched."""
    a = reviewed
    a.runtime()
    flying, flying_record = a.deploy()
    queued, queued_record = a.deploy()
    in_flight = a.start(flying)
    waiting = a.start(queued)
    a.fault("slow")
    a.approve(in_flight)
    wait_until(lambda: a.writes(flying_record), 60)
    owner = a.customer
    with Session(a.world.engine) as db, db.begin():
        db.execute(
            update(Membership)
            .where(Membership.workspace_id == owner.workspace, Membership.role == "owner")
            .values(role="viewer")
        )
    try:
        wait_until(
            lambda: _effect(a, in_flight) in ("accepted", "observed"), 60
        )  # it finished and recorded
        a.approve(waiting)
        time.sleep(8)
        assert a.commands(waiting) == [] and a.writes(queued_record) == []
    finally:
        with Session(a.world.engine) as db, db.begin():
            db.execute(
                update(Membership)
                .where(Membership.workspace_id == owner.workspace, Membership.role == "viewer")
                .values(role="owner")
            )
    assert len(a.writes(flying_record)) == 1


def _effect(a, execution_id):
    with Session(a.world.engine) as db:
        return db.scalar(
            select(ExecutionActionIntent.effect_state).where(
                ExecutionActionIntent.execution_id == execution_id
            )
        )


def test_spike_35_a_change_after_approval_is_refused_before_any_command(reviewed):
    """After `allow`: a tampered target artifact (an integrity case) or a revoked
    connection is refused before any command; zero writes."""
    a = reviewed
    revision, record = a.deploy()
    tampered = a.start(revision)
    a.approve(tampered)
    with Session(a.world.engine) as db, db.begin():
        intent = db.scalar(
            select(ExecutionActionIntent).where(ExecutionActionIntent.execution_id == tampered)
        )
        artifact = db.get(ExecutionArtifact, intent.target_artifact_id)
        content = bytearray(artifact.ciphertext)
        content[-1] ^= 1
        artifact.ciphertext = bytes(content)
    a.runtime()
    final = a.settled(tampered, ("failed", "reconciliation_required"), seconds=180)
    assert final["state"] in ("failed", "reconciliation_required")
    assert a.commands(tampered) == [] and a.writes(record) == []
    # Positive control: an untampered approval on the same connection writes once.
    revision, control_record = a.deploy()
    control = a.start(revision)
    a.approve(control)
    a.settled(control)
    assert len(a.writes(control_record)) == 1
