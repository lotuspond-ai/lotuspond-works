"""Action worker: claim, verify, journal, write once under a lease, report (E05).

Nothing in a delivery is trusted until its exact signed bytes verify under current,
separately provisioned command trust and name this runtime. Before a claim the
worker checks the signature and recipient; after it, E03's `validate_delivery`
checks the command against the pinned records the control plane serves only to the
lease holder, and the worker checks the installed connector pin, the operation's
write effect, the destination and the secret pins against its own provider.

The journal records `claimed`, then `started` durably before any connector code
runs, then the outcome about to be reported. A restarted worker never invokes a
journaled write again: a `started` one is reported as `stopped_unknown`, a
`claimed`-only one as `not_started`, a `finished` one with its journaled report,
each as historical evidence.

The lease is renewed outside the blocked call, from the worker's own thread, which
polls while the provider child runs. A write that has started is never cancelled by
its lease: a refused or lost renewal lets it finish. Its outcome is reported only
while the lease still holds; otherwise the journal keeps it and the control plane's
lapse leads to a read-back.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field

import rfc8785
from lotuspond_sdk.activity import UsageObservation
from lotuspond_sdk.executable.signing import (
    validate_command_signature_envelope,
    verify_runtime_command,
)
from lotuspond_sdk.executable.wire import (
    ActionReceipt,
    canonical_wire_json,
    validate_runtime_command,
)

from .action_invocation import WriteProviderSet, WriteRefused, invoke_write
from .action_protocol import (
    ACTION_RENEW_MS,
    MAX_RESPONSE_BYTES,
    ActionLease,
    ActionReport,
    ActionStop,
    validate_action_lease,
)
from .activity_invocation import fenced
from .activity_state import InvocationJournal
from .activity_worker import RENEW_MARGIN_MS, TRANSIENT, _survivor, boot_id
from .canonical import decode_fleet_json
from .commands import delivery_wire, validate_delivery
from .errors import FleetError
from .protocol import CommandDelivery, RuntimeIdentity
from .providers import ActivityFailure, unknown_usage, zero_usage
from .trust import validate_snapshot

KIND = "action"
_STATUSES = ("accepted", "observed_effect", "unknown")
_REF = 128


class LeaseLost(Exception):
    """The lease ended before the outcome could be reported; it stays journaled."""

    def __init__(self, outcome=None):
        super().__init__("lease_lost")
        self.outcome = outcome


class MountedActionSecrets:
    """A command's secret bindings through E03's mounted resolver."""

    def __init__(self, resolver):
        from .resolver import MountedSecretResolver

        if type(resolver) is not MountedSecretResolver:
            raise ValueError("A mounted resolver is required")
        self._resolver = resolver

    def resolve(self, binding) -> bytes:
        return self._resolver.resolve(binding.binding_id, binding.version)


@dataclass(frozen=True)
class ActionDependencies:
    """Everything an action needs; each piece is constructor-provided, none ambient.

    `checkpoint(point)` is a test seam at fixed crash points (`started`: after the
    journal holds `started`, before the provider child exists; `invoked`: after the
    child answered, before its outcome is journaled). It does nothing in production.
    """

    identity: RuntimeIdentity
    client: object
    trust: object
    providers: WriteProviderSet
    journal: InvocationJournal
    clock: object
    secrets: object = None
    renew_every_ms: int = ACTION_RENEW_MS
    retry_pause_s: float = field(default=0.25)
    checkpoint: object = None

    def __post_init__(self):
        RuntimeIdentity.model_validate(self.identity)
        if (
            type(self.providers) is not WriteProviderSet
            or type(self.journal) is not InvocationJournal
        ):
            raise ValueError("Write providers and journal are required")
        if not callable(getattr(self.clock, "now_ms", None)) or not callable(
            getattr(self.clock, "settled_ms", None)
        ):
            raise ValueError("A server-anchored clock is required")
        if not 1000 <= self.renew_every_ms <= ACTION_RENEW_MS:
            raise ValueError("Renewal cadence must be at most 20 s")
        if self.checkpoint is not None and not callable(self.checkpoint):
            raise ValueError("checkpoint must be callable")

    def at(self, point):
        if self.checkpoint is not None:
            self.checkpoint(point)


class ActionLeaseKeeper:
    """Keeps one action lease live from the worker's thread; never stops a write.

    `is_set()` is `call_provider`'s cancellation view: it renews when due and always
    answers False, so a started write runs to its own deadline. A `forbidden` renewal
    of a live lease (the control plane asks the action to stop) is remembered as
    `refused` and renewal stops; any other refusal, or time running out, is `lost`.
    """

    def __init__(self, deps: ActionDependencies, lease: ActionLease):
        self.deps, self.lease = deps, lease
        self.lost = self.refused = False
        self._due = self._next()

    def _next(self):
        now = self.deps.clock.now_ms()
        left = self.lease.expires_at_ms - now
        return now + max(50, min(self.deps.renew_every_ms, left // 2))

    def _live(self):
        try:
            return self.deps.clock.now_ms() < self.lease.expires_at_ms
        except FleetError:
            return False

    def tick(self):
        if self.lost:
            return
        if not self._live():
            self.lost = True
            return
        if self.refused:
            return
        try:
            now = self.deps.clock.now_ms()
            if now < self._due:
                return
            renewed = self.deps.client.renew(self.lease)
            renewed = validate_action_lease(renewed, now_ms=self.deps.clock.now_ms())
            if (renewed.attempt_id, renewed.command_id) != (
                self.lease.attempt_id,
                self.lease.command_id,
            ):
                raise FleetError("command_rejected")
            self.lease = renewed
            self._due = self._next()
        except FleetError as error:
            try:
                now = self.deps.clock.now_ms()
            except FleetError:
                now = None
            margin = now is not None and now < self.lease.expires_at_ms - RENEW_MARGIN_MS
            if error.code in TRANSIENT and margin:
                self._due = now + 500
            elif error.code == "forbidden" and self._live():
                self.refused = True
            elif not self._live() or error.code not in TRANSIENT:
                self.lost = True
        except Exception:
            self.lost = True  # anything unexpected ends the lease: fail closed

    def is_set(self) -> bool:
        self.tick()
        return False

    def held(self) -> bool:
        self.tick()
        return not self.lost


def _retry(call, deps, keeper=None, attempts=3):
    """Repeat an idempotent control call on transient failures while the lease holds."""
    for attempt in range(attempts):
        if keeper is not None and not keeper.held():
            raise LeaseLost
        try:
            return call()
        except FleetError as error:
            if error.code not in TRANSIENT or attempt == attempts - 1:
                raise
        time.sleep(deps.retry_pause_s * (attempt + 1))


def precheck(delivery: CommandDelivery, deps: ActionDependencies):
    """Before a claim: exact signed bytes, current trust and this runtime as recipient."""
    try:
        delivery_wire(delivery)
        command = validate_runtime_command(decode_fleet_json(delivery.command_bytes))
        if canonical_wire_json(command) != delivery.command_bytes:
            raise ValueError
        signature = validate_command_signature_envelope(decode_fleet_json(delivery.signature_bytes))
    except FleetError as error:
        if error.code == "input_limit":
            raise FleetError("command_rejected") from None
        raise
    except Exception:
        raise FleetError("command_rejected") from None
    identity, now = deps.identity, deps.clock.now_ms()
    if (
        command.installation_id != identity.installation_id
        or command.recovery_epoch != identity.recovery_epoch
        or command.workspace_id != identity.scope.workspace_id
        or command.project_id != identity.scope.project_id
        or command.environment_id != identity.scope.environment_id
        or command.worker_id != identity.instance_id
        or command.fencing_generation != identity.generation
        or not command.issued_at_ms <= now < command.expires_at_ms
    ):
        raise FleetError("command_rejected")
    snapshot = _snapshot(deps, now)
    if snapshot.revision < delivery.trust_revision:
        raise FleetError("trust_unavailable")
    if not verify_runtime_command(command, signature, snapshot.trust).valid:
        raise FleetError("command_rejected")
    return command


def _snapshot(deps, now):
    try:
        raw = deps.trust.current(deps.identity.installation_id)
    except Exception as error:
        _note(deps, "trust_" + type(error).__name__ + "_" + str(getattr(error, "code", "")))
        raise FleetError("trust_unavailable") from None
    try:
        return validate_snapshot(raw, now_ms=now)
    except FleetError as error:
        _note(deps, "snapshot_" + error.code)
        raise


def verify_records(delivery, records, deps: ActionDependencies):
    """E03's full command check against the pinned records, then this runtime's provider."""
    now = deps.clock.now_ms()
    command = validate_delivery(
        delivery,
        identity=deps.identity,
        records=records,
        trust=_snapshot(deps, now),
        now_ms=now,
    )
    binding, configuration = records.intent.binding, records.configuration
    # The exact installed connector artifact and an operation it declares as a write.
    descriptor = deps.providers.select(configuration.connector, binding.operation)
    if (
        descriptor.connector != configuration.connector
        or len(configuration.destinations) != 1
        or (configuration.secrets and deps.secrets is None)
    ):
        raise WriteRefused
    return command, descriptor


def _resolve(deps, records) -> list[str]:
    """Every pinned secret, resolved just before the call; any refusal denies it."""
    try:
        return [deps.secrets.resolve(binding).hex() for binding in records.secret_bindings]
    except Exception:
        raise FleetError("secret_unavailable") from None


def _usage(raw, deps, *, nothing_sent=False) -> UsageObservation:
    """Reported usage; zero when nothing was sent; otherwise unknown, never invented."""
    try:
        stamp = deps.clock.settled_ms()
    except FleetError:
        raise LeaseLost from None
    if type(raw) is dict:
        try:
            return UsageObservation.model_validate(dict(raw, completed_at_ms=stamp))
        except Exception:
            return unknown_usage(stamp)
    return zero_usage(stamp) if nothing_sent else unknown_usage(stamp)


def _operation_ref(response, binding) -> str:
    """The target's own record id when it gives a usable one, else the operation."""
    import re

    value = response.get("id") if type(response) is dict else None
    if (
        type(value) is str
        and len(value) <= _REF
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", value)
    ):
        return value
    return binding.operation


def digest(delivery: CommandDelivery) -> str:
    return hashlib.sha256(delivery.command_bytes).hexdigest()


def _note(deps, code):
    """Test mode: why a write stopped before its provider ran, beside each synthetic
    provider's call counter (a closed code, never a value)."""
    if not deps.providers.test_mode:
        return
    from .action_fixture import _failed

    for descriptor in deps.providers._found.values():
        if descriptor.test_only:
            _failed(descriptor.script_data() or {}, code, False)


def run_action(delivery, lease, deps: ActionDependencies, *, command=None):
    """Run one claimed action to its report or stop.

    Raises LeaseLost (carrying any outcome) when the lease ended before it could be
    reported. `command` passes on a command the caller has just prechecked.
    """
    command = command if command is not None else precheck(delivery, deps)
    lease = validate_action_lease(lease, now_ms=deps.clock.now_ms())
    if lease.command_id != command.command_id:
        raise FleetError("command_rejected")
    key, journal = command.command_id, deps.journal
    entry = journal.get(key, kind=KIND)
    if (
        entry is None
        or entry.get("unreadable")
        or entry["phase"] != "claimed"
        or entry.get("command_sha256") != digest(delivery)
    ):
        raise FleetError("conflict")  # possibly started before: never again
    journal.advance(
        key, "claimed", kind=KIND, attempt=lease.attempt_id, lease_revision=lease.lease_revision
    )
    keeper = ActionLeaseKeeper(deps, lease)
    nothing = zero_usage(deps.clock.settled_ms())

    def stopped(code, usage):
        stop = ActionStop(
            attempt_id=lease.attempt_id,
            lease_revision=keeper.lease.lease_revision,
            code=code,
            usage=usage,
        )
        if not keeper.held():
            raise LeaseLost(stop)
        return stop

    try:
        records = _retry(lambda: deps.client.records(delivery), deps, keeper)
        command, descriptor = verify_records(delivery, records, deps)
        if not keeper.held():
            raise LeaseLost
        secrets = _resolve(deps, records)
    except FleetError as error:
        if error.code == "unsafe_storage":
            raise
        _note(deps, "before_" + error.code)
        return stopped("not_started", nothing)  # refused before anything ran
    binding = records.intent.binding
    idempotency_key = (
        binding.action_id if descriptor.idempotency(binding.operation) == "target_key" else None
    )
    if fenced() or journal.fenced():
        _note(deps, "fenced_in_process" if fenced() else "fenced_journal")
        return stopped("not_started", nothing)  # an unconfirmed cleanup stops new writes
    journal.advance(key, "started", kind=KIND)
    deps.at("started")
    deadline_ms = keeper.lease.deadline_ms
    try:
        outcome = invoke_write(
            descriptor,
            records,
            idempotency_key,
            secrets,
            test_mode=deps.providers.test_mode,
            cancel=keeper,
            deadline_ns=time.monotonic_ns() + max(0, deadline_ms - deps.clock.now_ms()) * 1_000_000,
            deadline_ms=deadline_ms,
            fence=journal.fence,
            spawned=lambda group: journal.advance(
                key, "started", kind=KIND, group=group, boot=boot_id()
            ),
            counter_id=key,
        )
    except ActivityFailure:
        # Refused before any child existed (a fence set by another worker thread
        # after the check above): nothing was sent.
        return stopped("not_started", nothing)
    del secrets
    deps.at("invoked")
    if outcome.reply is None and (not outcome.started or outcome.reached is False):
        return stopped("not_started", _usage(outcome.usage, deps, nothing_sent=True))
    reply = outcome.reply or {}
    status = reply.get("status") if reply.get("status") in _STATUSES else "unknown"
    response = reply.get("response") if outcome.reply is not None else None
    usage = _usage(reply.get("usage") if outcome.reply is not None else outcome.usage, deps)
    handle = None
    if response is not None:
        content = rfc8785.dumps(response)
        if len(content) <= MAX_RESPONSE_BYTES:
            try:
                handle = _retry(lambda: deps.client.store(lease.attempt_id, content), deps, keeper)
            except FleetError:
                handle = None  # the receipt still reports what the target answered
            except LeaseLost:
                handle = None
    report = ActionReport(
        attempt_id=lease.attempt_id,
        lease_revision=keeper.lease.lease_revision,
        receipt=ActionReceipt(
            receipt_id=lease.attempt_id,
            action_id=binding.action_id,
            intent_sha256=command.intent_sha256,
            attempt=lease.ordinal,
            target_operation_ref=_operation_ref(response, binding),
            status=status,
            evidence_refs=[],
        ),
        response=handle,
        usage=usage,
    )
    if not keeper.held():
        raise LeaseLost(report)
    return report


def _send(deps, outcome):
    if type(outcome) is ActionReport:
        _retry(lambda: deps.client.receipt(outcome), deps)
    else:
        _retry(lambda: deps.client.stop(outcome), deps)


def _status(outcome) -> str:
    return outcome.receipt.status if type(outcome) is ActionReport else outcome.code


def _outcome(raw):
    if type(raw) is not dict:
        return None
    try:
        return (ActionReport if "receipt" in raw else ActionStop).model_validate(raw)
    except Exception:
        return None


class ActionWorker:
    """Polls, claims and runs one action at a time; reports what it holds."""

    def __init__(self, deps: ActionDependencies):
        self.deps = deps
        self._recovered = False

    def report(self, key, outcome) -> str:
        """Journal the outcome, then report it; an equal repeat is idempotent."""
        journal = self.deps.journal
        raw = json.loads(outcome.model_dump_json())
        journal.advance(key, "finished", kind=KIND, outcome=raw)
        try:
            _send(self.deps, outcome)
        except FleetError:
            return "unreported"  # the journal keeps it as reconciliation evidence
        journal.advance(
            key,
            "recorded",
            kind=KIND,
            result_sha256=hashlib.sha256(rfc8785.dumps(raw)).hexdigest(),
        )
        return _status(outcome)

    def recover(self) -> bool:
        """Report journaled actions as historical evidence; never rerun any of them.

        `started` without an outcome is `stopped_unknown` (and a surviving provider
        group fences the worker); `claimed` alone is `not_started`; `finished` is its
        journaled report. An entry is abandoned only when it names no lease, cannot be
        read or is refused outright. When time or the control plane is unavailable it
        keeps its phase, and the result is False: the worker tries again on its next
        round.
        """
        journal, deps = self.deps.journal, self.deps
        complete = True
        for entry in journal.entries(KIND):
            phase, key = entry["phase"], entry["attempt_id"]
            if phase in ("recorded", "abandoned"):
                continue
            outcome = None
            attempt, revision = entry.get("attempt"), entry.get("lease_revision")
            try:
                stamp = deps.clock.settled_ms()
            except FleetError:
                complete = False  # no time to stamp a stop with: keep it for later
                continue
            if phase == "finished":
                outcome = _outcome(entry.get("outcome"))
            elif attempt is not None and revision is not None:
                started = phase == "started"
                outcome = ActionStop(
                    attempt_id=attempt,
                    lease_revision=revision,
                    code="stopped_unknown" if started else "not_started",
                    usage=unknown_usage(stamp) if started else zero_usage(stamp),
                )
            if phase == "started" and _survivor(entry):
                journal.fence()  # its provider may still run: no new work
            if outcome is not None and not entry.get("unreadable"):
                if phase != "finished":
                    journal.advance(
                        key, "finished", kind=KIND, outcome=json.loads(outcome.model_dump_json())
                    )
                try:
                    _send(deps, outcome)
                except FleetError as error:
                    if error.code in TRANSIENT or error.code == "unsafe_storage":
                        if error.code == "unsafe_storage":
                            raise
                        complete = False  # not now: reported on a later round
                    else:
                        journal.advance(key, "abandoned", kind=KIND)
                else:
                    journal.advance(key, "recorded", kind=KIND)
            else:
                journal.advance(key, "abandoned", kind=KIND)
        return complete

    def fenced(self) -> bool:
        if fenced():
            self.deps.journal.fence()
        return self.deps.journal.fenced()

    def run_once(self) -> str:
        if not self._recovered:
            self._recovered = self.recover()
        if self.fenced():
            return "fenced"
        deps = self.deps
        delivery = deps.client.poll()
        if delivery is None:
            return "idle"
        try:
            command = precheck(delivery, deps)
        except FleetError as error:
            if error.code != "command_rejected":
                raise  # local: trust, identity or time, which fails every delivery alike
            # Altered, misaddressed or badly signed: never claimed. Without a decline
            # route it expires on its own within the command lifetime (5 minutes).
            return "refused"
        key = command.command_id
        existing = deps.journal.get(key, kind=KIND)
        if existing is not None:
            return "refused"  # seen before: never claimed or invoked twice
        deps.journal.advance(key, "claimed", kind=KIND, command_sha256=digest(delivery))
        try:
            lease = _retry(lambda: deps.client.claim(delivery), deps)
        except FleetError as error:
            if error.code == "unsafe_storage":
                raise
            deps.journal.advance(key, "abandoned", kind=KIND)
            return "refused"
        try:
            outcome = run_action(delivery, lease, deps, command=command)
        except LeaseLost as lost:
            # Kept in its phase (with any outcome), so recovery reports it as
            # historical evidence; the control plane's lapse leads to a read-back.
            if lost.outcome is not None:
                deps.journal.advance(
                    key, "finished", kind=KIND, outcome=json.loads(lost.outcome.model_dump_json())
                )
            self._recovered = False
            self.fenced()
            return "lost"
        status = self.report(key, outcome)
        if status == "unreported":
            self._recovered = False  # retried as historical evidence on the next round
        self.fenced()
        return status
