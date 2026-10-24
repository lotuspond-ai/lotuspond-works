"""Activity worker: verify, journal, invoke under a lease, report (E04).

Nothing in a delivery is trusted until the exact signed bytes verify under current,
separately provisioned activity trust and match this runtime's identity and the
pinned records. The invocation journal records `started` durably before any
provider is reached, so a restarted worker never invokes an attempt twice. Time is
the control plane's: confirmed server time plus elapsed monotonic time.

The control transport carries one request at a time, so leases are renewed on the
worker's own thread, where it already waits: inside the provider loop and before
each control call. Losing the lease cancels the provider child at once and leaves
the outcome uncertain. A renewal refused while the lease still holds is a request
to stop (a pause or a cancellation): the provider child is cancelled just as fast,
and the outcome is reported as `cancelled`. Once a lease is held, every other
ending is reported: an outcome nobody could have caused (nothing reached a
provider) with zero usage, a known one with the usage observed.
"""

from __future__ import annotations

import hashlib
import math
import os
import time
from dataclasses import dataclass, field
from typing import Protocol

import rfc8785
from lotuspond_sdk.activity import (
    ActivityDelivery,
    ActivityEnvelope,
    UsageObservation,
    canonical_activity_json,
    validate_activity,
    verify_activity,
)
from lotuspond_sdk.activity.models import ActivitySignature
from lotuspond_sdk.executable.input import decode_executable_bytes
from lotuspond_sdk.executable.types import resolve_schema, validate_value
from lotuspond_sdk.trust import TrustSet

from .activity_invocation import call_provider, fenced, require
from .activity_protocol import (
    MAX_VALUE_BYTES,
    ActivityLease,
    ActivityResult,
    PinnedActivityRecords,
    validate_lease,
)
from .activity_state import InvocationJournal
from .control_transport import ATTEMPT_SECONDS
from .errors import FleetError
from .protocol import RuntimeIdentity, SecretPin
from .providers import ActivityFailure, ProviderSet, unknown_usage, zero_usage

RENEW_EVERY_MS = 20000
# Renewal stops being retried in time to stop the provider before the lease the
# control plane granted runs out: one more control request may block for the
# transport's bound, then stopping takes 1 s (TERM) and 1 s (KILL), plus 1 s.
RENEW_MARGIN_MS = int(ATTEMPT_SECONDS * 1000) + 3000
# Codes that say "not now" rather than "no": the call may be repeated.
TRANSIENT = frozenset({"transport_failed", "rate_limited", "authority_unavailable"})
# Declines without one verified delivery in between before the worker stops declining.
MAX_DECLINES_IN_A_ROW = 3
# The record source's answers for a delivery it will not serve (Task 12's route).
# (`invalid_request` is not one: it may answer this runtime's own malformed request.)
RECORD_REFUSALS = frozenset({"forbidden", "not_found", "conflict"})
# Failures meaning this runtime cannot serve the attempt now, so a new ordinal may.
_UNAVAILABLE = TRANSIENT | {"trust_unavailable", "secret_unavailable", "unauthenticated"}


class ServerClock:
    """Confirmed server time plus elapsed monotonic time, never local wall time.

    Anchored from a response that carried the server's time (an E03 session or
    heartbeat), as `RuntimeSupervisor` does; re-anchoring on every such response
    keeps it close. Two bounds are kept:

    - `now_ms` counts the whole roundtrip as the response's age and adds a drift
      allowance, so it never runs behind the server: every validity check uses it.
    - `settled_ms` counts none of the roundtrip and subtracts the allowance, so it
      never runs ahead: usage is stamped with it, because the server refuses
      completions in its future.

    The allowance covers clock-rate drift between the two hosts and millisecond
    rounding: 50 ms plus 1 ms per second since the anchor (twice NTP's maximum
    slew). A wall clock that disagrees with monotonic time by more than 30 s (a
    suspend, a jump), or an anchor older than 10 minutes, makes time unavailable:
    the runtime re-anchors on every session or heartbeat response.
    """

    DRIFT_BASE_MS = 50
    DRIFT_MS_PER_S = 1
    # An anchor older than this makes time unavailable rather than ever wider.
    MAX_ANCHOR_AGE_S = 600

    def __init__(self, *, monotonic=time.monotonic, wall_ms=lambda: time.time_ns() // 1_000_000):
        self._monotonic, self._wall = monotonic, wall_ms
        self._anchor = None

    def anchor(self, server_time_ms: int, *, sent: float, received: float):
        if type(server_time_ms) is not int or server_time_ms < 0 or not 0 <= received - sent <= 10:
            raise FleetError("authority_unavailable")
        self._anchor = (server_time_ms, sent, received, self._wall())

    def _bounds(self):
        if self._anchor is None:
            raise FleetError("authority_unavailable")
        server, sent, received, wall = self._anchor
        now = self._monotonic()
        if (
            now < received
            or now - received > self.MAX_ANCHOR_AGE_S
            or abs(self._wall() - (wall + (now - received) * 1000)) > 30000
        ):
            raise FleetError("authority_unavailable")
        drift = self.DRIFT_BASE_MS + math.ceil((now - received) * self.DRIFT_MS_PER_S)
        latest = server + math.ceil((now - sent) * 1000) + drift
        earliest = server + math.floor((now - received) * 1000) - drift
        return earliest, latest

    def now_ms(self) -> int:
        return self._bounds()[1]

    def settled_ms(self) -> int:
        return max(0, self._bounds()[0])


class ActivityRecordSource(Protocol):
    """Pinned records and agent declarations for a verified envelope (control plane)."""

    def load(self, envelope: ActivityEnvelope) -> PinnedActivityRecords: ...

    def agent(self, envelope: ActivityEnvelope): ...


class ValueSource(Protocol):
    def fetch(self, attempt_id: str, handle) -> bytes: ...


class SecretSource(Protocol):
    def resolve(self, pin: SecretPin) -> bytes: ...


class DenyRecordSource:
    def load(self, envelope):
        raise FleetError("authority_unavailable")

    def agent(self, envelope):
        raise FleetError("authority_unavailable")


class DenyValueSource:
    def fetch(self, attempt_id, handle):
        raise FleetError("authority_unavailable")


class MountedActivitySecrets:
    """Activity secret pins through E03's mounted resolver and its operator mapping.

    `bindings` maps each pinned (reference, version) to the operator's binding ID,
    exactly as E03 commands resolve their connection secrets; an unmapped pin is
    unavailable.
    """

    def __init__(self, resolver, bindings: dict):
        from .resolver import MountedSecretResolver

        if type(resolver) is not MountedSecretResolver or type(bindings) is not dict:
            raise ValueError("A mounted resolver and its bindings are required")
        self._resolver, self._bindings = resolver, dict(bindings)

    def resolve(self, pin: SecretPin) -> bytes:
        pin = SecretPin.model_validate(pin)
        binding = self._bindings.get((pin.reference_id, pin.version))
        if binding is None:
            raise FleetError("secret_unavailable")
        return self._resolver.resolve(binding, pin.version)


@dataclass(frozen=True)
class ActivityDependencies:
    """Everything an activity needs; each piece is constructor-provided, none ambient."""

    identity: RuntimeIdentity
    client: object
    records: object
    values: object
    trust: object
    providers: ProviderSet
    journal: InvocationJournal
    clock: object
    context: object = None
    secrets: object = None
    renew_every_ms: int = RENEW_EVERY_MS
    retry_pause_s: float = field(default=0.25)

    def __post_init__(self):
        RuntimeIdentity.model_validate(self.identity)
        if type(self.providers) is not ProviderSet or type(self.journal) is not InvocationJournal:
            raise ValueError("Providers and journal are required")
        if not callable(getattr(self.clock, "now_ms", None)) or not callable(
            getattr(self.clock, "settled_ms", None)
        ):
            raise ValueError("A server-anchored clock is required")
        if not 1000 <= self.renew_every_ms <= RENEW_EVERY_MS:
            raise ValueError("Renewal cadence must be at most 20 s")


class LocalMismatch(FleetError):
    """Refused against this runtime's own identity or clock, not for the delivery.

    Every delivery would fail alike (a stale identity, a wrong anchor), so it is
    never a reason to decline one; the code stays `command_rejected`.
    """

    def __init__(self):
        super().__init__("command_rejected")


def declinable(error) -> bool:
    """A refusal that is about the delivery itself, never about local state."""
    return error.code == "command_rejected" and type(error) is not LocalMismatch


class LeaseLost(Exception):
    """The lease ended before an outcome; the attempt stays uncertain server-side."""


class LeaseKeeper:
    """Keeps one lease live from the worker's own thread.

    `tick()` renews when due and reports whether the provider may go on: the lease
    (and its parent's, for a child) is held and nothing asked it to stop. A transient
    failure is retried until too little of the lease is left to stop the provider in
    time; any other refusal ends the lease at once (`lost`). The exception is a
    `forbidden` renewal while the lease is still live: the control plane asks the
    attempt to stop (a pause or a cancellation), so it is `cancelled`. The provider
    stops and the outcome is reported with what was observed, under the lease that
    still holds. `held()` is whether the lease still holds for reporting. `is_set()`
    is the cancellation view for `call_provider`, which polls it every 50 ms.
    """

    def __init__(self, deps: ActivityDependencies, lease: ActivityLease, parent=None):
        self.deps, self.lease, self.parent = deps, lease, parent
        self.lost = self.cancelled = False
        self._due = self._next()

    def _next(self):
        now = self.deps.clock.now_ms()
        left = self.lease.expires_at_ms - now
        return now + max(50, min(self.deps.renew_every_ms, left // 2))

    def _renew(self):
        try:
            now = self.deps.clock.now_ms()
            if now < self._due:
                return
            renewed = self.deps.client.renew(self.lease)
            # Judged on the clock after the answer: the server's time when it granted it.
            renewed = validate_lease(renewed, now_ms=self.deps.clock.now_ms())
            if (renewed.attempt_id, renewed.generation) != (
                self.lease.attempt_id,
                self.lease.generation,
            ):
                raise FleetError("command_rejected")
            self.lease = renewed
            self._due = self._next()
        except FleetError as error:
            try:
                now = self.deps.clock.now_ms()
            except FleetError:
                now = None
            live = now is not None and now < self.lease.expires_at_ms - RENEW_MARGIN_MS
            if error.code in TRANSIENT and live:
                self._due = now + 500
                return
            if error.code == "forbidden" and live:
                self.cancelled = True  # asked to stop: report, under the live lease
                return
            self.lost = True
        except Exception:
            self.lost = True  # anything unexpected ends the lease: fail closed

    def tick(self, *, now=False) -> bool:
        """Renew when due (`now`: at once); True while the provider may go on."""
        if self.parent is not None and not self.parent.tick():
            # A parent asked to stop stops its child too; a lost one loses it.
            if self.parent.lost:
                self.lost = True
            else:
                self.cancelled = True
        if not (self.lost or self.cancelled):
            if now:
                self._due = 0
            self._renew()
        return not (self.lost or self.cancelled)

    def held(self) -> bool:
        """Whether the lease still holds for control calls and reporting."""
        self.tick()
        return not self.lost

    def is_set(self) -> bool:
        return not self.tick()


def running(keeper):
    """Go on only while the provider may: LeaseLost, or `cancelled` with nothing started."""
    if not keeper.tick():
        if keeper.lost:
            raise LeaseLost
        raise ActivityFailure("cancelled", reached=False)


def retry(call, deps, keeper=None, attempts=3):
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


def failure_code(error: FleetError) -> str:
    return "unavailable" if error.code in _UNAVAILABLE else "provider_failed"


def _canonical(value) -> bytes:
    content = rfc8785.dumps(value)
    if len(content) > MAX_VALUE_BYTES:
        raise ActivityFailure("invalid_output")
    return content


def _typed(value, schema):
    """Validate against a pinned `{"schema", "definitions"}` record; None means bounded only."""
    try:
        if schema is None or schema.get("schema") is None:
            return value
        definitions = schema.get("definitions", [])
        resolved = resolve_schema(schema["schema"], definitions)
        return validate_value(value, resolved, [])
    except Exception:
        raise ActivityFailure("invalid_output") from None


def verify_delivery(delivery, deps: ActivityDependencies):
    """Exact signed bytes, current trust, this runtime as recipient, pinned records."""
    try:
        delivery = ActivityDelivery.model_validate(delivery)
        envelope = validate_activity(decode_executable_bytes(delivery.envelope.encode("utf-8")))
        if canonical_activity_json(envelope) != delivery.envelope.encode("utf-8"):
            raise ValueError
        signature = ActivitySignature.model_validate_json(delivery.signature)
    except Exception:
        raise FleetError("command_rejected") from None
    identity = deps.identity
    now = deps.clock.now_ms()
    if (
        envelope.installation_id != identity.installation_id
        or envelope.recovery_epoch != identity.recovery_epoch
        or envelope.workspace_id != identity.scope.workspace_id
        or envelope.project_id != identity.scope.project_id
        or envelope.environment_id != identity.scope.environment_id
        or envelope.pool_id != identity.pool_id
        or envelope.instance_id != identity.instance_id
        or envelope.generation != identity.generation
        or not envelope.issued_at_ms <= now < envelope.expires_at_ms
    ):
        raise LocalMismatch
    try:
        trust = TrustSet.model_validate(deps.trust.current(identity.scope, now))
        report = verify_activity(envelope, signature, trust)
    except Exception:
        raise FleetError("trust_unavailable") from None
    if not report.valid:
        # A signer this runtime's trust does not know is a local trust gap (stale
        # after a rotation): every later delivery fails alike, so it is not a reason
        # to decline one. Any other invalid signature is a bad delivery.
        raise FleetError(
            "trust_unavailable" if report.code == "untrusted_key" else "command_rejected"
        )
    try:
        records = deps.records.load(envelope)
    except FleetError as error:
        # The control plane will not serve this delivery: bad for this runtime. An
        # outage or an expired session is local and stands as it is.
        if error.code in RECORD_REFUSALS:
            raise FleetError("command_rejected") from None
        raise
    except Exception:
        raise FleetError("authority_unavailable") from None
    if (
        type(records) is not PinnedActivityRecords
        or records.envelope != envelope
        or records.dependency != envelope.dependency
        or records.request_sha256 != envelope.input.sha256
        or envelope.expires_at_ms > records.deadline_ms
    ):
        raise FleetError("command_rejected")
    return envelope, records


def delivery_digest(delivery) -> str:
    return hashlib.sha256(delivery.envelope.encode("utf-8")).hexdigest()


def _resumable(entry, digest, generation) -> bool:
    """Only a claim for this exact delivery that never started may be (re)claimed."""
    return (
        entry.get("phase") == "claimed"
        and not entry.get("unreadable")
        and entry.get("delivery_sha256") == digest
        and entry.get("generation") == generation
    )


def decline(deps, delivery) -> bool:
    """Refuse a delivery this runtime will not run, so it stops heading its polls.

    Best effort: the control plane accepts it only for a pending delivery it
    dispatched to this session, named by its exact digest. When it does not, the
    delivery still expires on its own (at most 5 minutes).
    """
    try:
        retry(lambda: deps.client.decline(delivery), deps)
    except FleetError:
        return False
    return True


def checked_lease(lease, envelope, deps) -> ActivityLease:
    lease = validate_lease(lease, now_ms=deps.clock.now_ms())
    if lease.attempt_id != envelope.attempt_id or lease.generation != envelope.generation:
        raise FleetError("command_rejected")
    return lease


def _deadline_ns(deps, deadline_ms):
    return time.monotonic_ns() + max(0, deadline_ms - deps.clock.now_ms()) * 1_000_000


def _fetch_input(deps, envelope, records):
    """The actual request: its bytes must match the signed digest exactly."""
    try:
        content = deps.values.fetch(envelope.attempt_id, envelope.input)
    except Exception:
        raise ActivityFailure("unavailable", reached=False) from None
    if type(content) is not bytes or hashlib.sha256(content).hexdigest() != records.request_sha256:
        raise FleetError("command_rejected")
    return decode_executable_bytes(content)


def read_arguments(value, records):
    """A connector read's `{"target", "input"}`, typed by the operation's schemas."""
    try:
        schemas = records.input_schema
        if type(value) is not dict or set(value) != {"target", "input"}:
            raise ValueError
        definitions = schemas.get("definitions", [])
        for key in ("target", "input"):
            _typed(value[key], {"schema": schemas[key], "definitions": definitions})
    except Exception:
        raise ActivityFailure("invalid_output", reached=False) from None
    return value


def _resolve(deps, pins) -> list[str]:
    """Every pinned secret, resolved just before the call; any refusal denies it."""
    if not pins:
        return []
    if deps.secrets is None:
        raise ActivityFailure("unavailable", reached=False)
    try:
        return [deps.secrets.resolve(pin).hex() for pin in pins]
    except Exception:
        raise ActivityFailure("unavailable", reached=False) from None


def _descriptor(descriptor, deps) -> dict:
    return {
        "provider_id": descriptor.provider_id,
        "dependency": descriptor.dependency.model_dump(mode="json"),
        "effect": descriptor.effect,
        "operations": sorted(descriptor.operations),
        "test_only": descriptor.test_only,
        "script": descriptor.script_data() if deps.providers.test_mode else None,
        "connector": (
            None if descriptor.connector is None else descriptor.connector.model_dump(mode="json")
        ),
    }


def _records(records: PinnedActivityRecords) -> dict:
    return {
        "envelope": records.envelope.model_dump(mode="json"),
        "connection": (
            None if records.connection is None else records.connection.model_dump(mode="json")
        ),
        "dependency": records.dependency.model_dump(mode="json"),
        "deadline_ms": records.deadline_ms,
        "input_schema": records.input_schema,
        "output_schema": records.output_schema,
        "request_sha256": records.request_sha256,
    }


def invoke_read(deps, envelope, records, arguments, cancel, deadline_ms):
    """One connector read in a supervised child, under the pinned connection's rules.

    The connection must name the installed connector this provider serves and allow
    the operation; its request bound and timeout apply to the call and all of its
    secrets are resolved just before it. Secrets exist only in this request's memory
    and on the child's socket: never logged, stored or journaled. The response bound
    is applied by `bounded_response`.
    """
    if envelope.kind != "connector_read" or records.dependency != envelope.dependency:
        raise ActivityFailure("unavailable", reached=False)
    descriptor = deps.providers.select(records.dependency, "read", envelope.operation)
    connection = records.connection
    if (
        connection is None
        or connection.connector != descriptor.connector
        or (connection.connector.name, connection.connector.version)
        != (records.dependency.name, records.dependency.version)
        or envelope.operation not in connection.operations
    ):
        raise ActivityFailure("unavailable", reached=False)
    limits = connection.data_handling
    if len(rfc8785.dumps(arguments)) > limits.max_request_bytes:
        raise ActivityFailure("provider_failed", reached=False)
    deadline_ms = min(deadline_ms, deps.clock.now_ms() + limits.timeout_ms)
    request = {
        "descriptor": _descriptor(descriptor, deps),
        "test_mode": deps.providers.test_mode,
        "records": _records(records),
        "arguments": arguments,
        "deadline_ms": deadline_ms,
        "secrets": _resolve(deps, (*connection.secrets, *descriptor.credentials)),
    }
    return call_provider(
        request,
        deadline_ns=_deadline_ns(deps, deadline_ms),
        cancel=cancel,
        fence=deps.journal.fence,
        spawned=_spawned(deps, envelope.attempt_id),
    )


def boot_id() -> str | None:
    """This boot's identity (Linux), so a recorded process group is never confused
    with one of the same number after a reboot."""
    try:
        with open("/proc/sys/kernel/random/boot_id", encoding="ascii") as stream:
            return stream.read(64).strip() or None
    except OSError:
        return None


def _spawned(deps, attempt_id):
    """Journal the provider's process group before it receives its request."""
    return lambda group: deps.journal.advance(attempt_id, "started", group=group, boot=boot_id())


def _survivor(entry) -> bool:
    """A recorded provider group of this boot that still exists (ours: same user)."""
    group = entry.get("group")
    if type(group) is not int or group <= 1 or entry.get("boot") != boot_id():
        return False
    try:
        os.killpg(group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return False  # another user's group: the number was reused, ours is gone
    return True


def bounded_response(value, records):
    """A read's value within the connection's response bound and the value bound."""
    limit = records.connection.data_handling.max_response_bytes
    if len(rfc8785.dumps(value)) > min(limit, MAX_VALUE_BYTES):
        raise ActivityFailure("invalid_output")
    return value


def invoke_model(deps, descriptor, request, cancel, deadline_ms, *, attempt_id=None):
    """One model round in a supervised child; its credentials resolved just before."""
    payload = {
        "descriptor": _descriptor(descriptor, deps),
        "test_mode": deps.providers.test_mode,
        "model_request": request.to_json(),
        "deadline_ms": deadline_ms,
        "secrets": _resolve(deps, descriptor.credentials),
    }
    return call_provider(
        payload,
        deadline_ns=_deadline_ns(deps, deadline_ms),
        cancel=cancel,
        fence=deps.journal.fence,
        spawned=None if attempt_id is None else _spawned(deps, attempt_id),
    )


def observed_usage(outcome, deps) -> UsageObservation:
    """What a call cost: reported, zero when nothing was sent, otherwise unknown.

    Completion is stamped on the server-anchored clock, never the provider's. If
    time is unavailable once a provider may have run, nothing is reported (lease
    loss): the control plane's lapse leaves the attempt to reconciliation, never zero.
    """
    try:
        stamp = deps.clock.settled_ms()
    except FleetError:
        raise LeaseLost from None
    raw = outcome.reply.get("usage") if outcome.reply is not None else outcome.usage
    if type(raw) is dict:
        try:
            return UsageObservation.model_validate(dict(raw, completed_at_ms=stamp))
        except Exception:
            return unknown_usage(stamp)  # never invented as zero
    if not outcome.started or outcome.reached is False:
        return zero_usage(stamp)
    return unknown_usage(stamp)


def zero(deps) -> UsageObservation:
    return zero_usage(deps.clock.settled_ms())


def run_activity(delivery, lease, deps: ActivityDependencies, *, verified=None) -> ActivityResult:
    """Run one claimed activity to its outcome; LeaseLost when the lease ended first.

    `verified` passes on the envelope and records a caller has just verified, so a
    delivery is checked once per claim.
    """
    envelope, records = verified if verified is not None else verify_delivery(delivery, deps)
    lease = checked_lease(lease, envelope, deps)
    digest = delivery_digest(ActivityDelivery.model_validate(delivery))
    existing = deps.journal.get(envelope.attempt_id)
    if existing is not None and not _resumable(existing, digest, envelope.generation):
        raise FleetError("conflict")  # possibly started before: never again
    # Stamped before anything starts: a clock failure later never turns into zero.
    nothing = zero(deps)
    deps.journal.advance(
        envelope.attempt_id, "started", delivery_sha256=digest, generation=envelope.generation
    )
    keeper = LeaseKeeper(deps, lease)

    def ended(code, usage):
        if keeper.lost:
            raise LeaseLost
        return ActivityResult(
            attempt_id=envelope.attempt_id,
            lease_revision=keeper.lease.lease_revision,
            result=None,
            code=code,
            usage=usage,
        )

    # Until a provider is reached nothing was spent: zero is known. Once one is
    # reached without a usage report, usage stays unknown, never zero.
    usage, reached = nothing, False
    try:
        value = _fetch_input(deps, envelope, records)
        running(keeper)
        deadline = min(records.deadline_ms, keeper.lease.deadline_ms)
        if envelope.kind == "connector_read":
            arguments = read_arguments(value, records)
            outcome = invoke_read(deps, envelope, records, arguments, keeper, deadline)
            reached = outcome.started
            usage = observed_usage(outcome, deps)
            output = bounded_response(require(outcome).get("value"), records)
        else:
            from .agent import AgentRequest, ProposalRefused, run_agent

            agent = AgentRequest.from_records(deps, envelope, records, value)
            running(keeper)
            try:
                result = run_agent(agent, deps, keeper)
            except ProposalRefused:
                # The model proposed an undeclared tool or other arguments.
                raise ActivityFailure("invalid_output") from None
            # Output exists only after at least one model round ran.
            output, usage, reached = result.value, result.usage, True
        if keeper.lost:
            raise LeaseLost
        output = _typed(output, records.output_schema)
        content = _canonical(output)
        handle = retry(lambda: deps.client.store(envelope.attempt_id, content), deps, keeper)
        if keeper.lost:
            raise LeaseLost
        return ActivityResult(
            attempt_id=envelope.attempt_id,
            lease_revision=keeper.lease.lease_revision,
            result=handle,
            code="succeeded",
            usage=usage,
        )
    except ActivityFailure as failure:
        return ended(failure.code, usage)
    except LeaseLost:
        raise
    except FleetError as error:
        if error.code == "unsafe_storage":
            raise  # the journal cannot be trusted: stop, never report around it
        # Once a provider may have run, a failure is final: a retry would run it again.
        return ended("provider_failed" if reached else failure_code(error), usage)


class ActivityWorker:
    """Polls, claims and runs one activity at a time; reports outcomes it holds."""

    def __init__(self, deps: ActivityDependencies):
        self.deps = deps
        self._recovered = False

    def recover(self):
        """After a restart: whatever may have started is never invoked again."""
        journal = self.deps.journal
        ambiguous = journal.ambiguous()
        for attempt_id in ambiguous:
            if _survivor(journal.get(attempt_id)):
                # Its provider may still run after a worker crash: no new work until
                # an operator confirms it is gone and removes the fence.
                journal.fence()
            journal.advance(attempt_id, "abandoned")
        return ambiguous

    def fenced(self) -> bool:
        """An unconfirmed cleanup, now or before a restart, stops all provider work."""
        if fenced():
            self.deps.journal.fence()
        return self.deps.journal.fenced()

    def report(self, result: ActivityResult) -> str:
        """Journal the outcome, then report it; an equal repeat is idempotent."""
        journal = self.deps.journal
        outcome = result.model_dump(mode="json")
        journal.advance(result.attempt_id, "finished", outcome=outcome)
        try:
            retry(lambda: self.deps.client.result(result), self.deps)
        except FleetError:
            # The lease lapses; the journal keeps the outcome as reconciliation evidence.
            return "unreported"
        journal.advance(
            result.attempt_id,
            "recorded",
            result_sha256=hashlib.sha256(rfc8785.dumps(outcome)).hexdigest(),
        )
        return result.code

    def run_once(self) -> str:
        if not self._recovered:
            # Once per worker, before its first poll: ambiguous attempts are abandoned
            # and a surviving provider group fences the worker. One worker holds the
            # journal (its lock), so one worker per process.
            self.recover()
            self._recovered = True
        if self.fenced():
            return "fenced"
        deps = self.deps
        delivery = deps.client.poll()
        if delivery is None:
            return "idle"
        try:
            envelope, records = verify_delivery(delivery, deps)
        except FleetError as error:
            # This delivery itself is bad (altered, badly signed, not matching its
            # records, or refused by the record source): declined, so it stops
            # heading the polls and another runtime or a new ordinal may serve it.
            # Local state (trust, identity, clock, outages) fails every delivery
            # alike and declines nothing. A circuit breaker stops declining after a
            # few in a row without one verified delivery, so a runtime broken in a
            # way not caught here cannot fail its pool's work at poll rate.
            # The count is journaled, so a new worker or a restart cannot reset it,
            # and counted before the decline is sent, so a crash never loses one.
            if declinable(error) and deps.journal.declines_in_a_row() < MAX_DECLINES_IN_A_ROW:
                deps.journal.count_decline()
                if decline(deps, delivery):
                    return "declined"
            raise
        deps.journal.clear_declines()
        digest = delivery_digest(delivery)
        existing = deps.journal.get(envelope.attempt_id)
        if existing is not None and not _resumable(existing, digest, envelope.generation):
            return "refused"  # seen past its claim: never claimed or invoked twice
        deps.journal.advance(
            envelope.attempt_id, "claimed", delivery_sha256=digest, generation=envelope.generation
        )
        # A lost acknowledgement is safe to repeat: an equal claim returns the same lease.
        lease = retry(lambda: deps.client.claim(delivery), deps)
        try:
            result = run_activity(delivery, lease, deps, verified=(envelope, records))
        except LeaseLost:
            deps.journal.advance(envelope.attempt_id, "abandoned")
            self.fenced()
            return "lost"
        except FleetError as error:
            if (
                error.code == "unsafe_storage"
                or deps.journal.get(envelope.attempt_id)["phase"] != "claimed"
            ):
                raise
            # Refused before anything started (a lease out of bounds): nothing ran.
            try:
                revision = ActivityLease.model_validate(lease).lease_revision
            except Exception:
                raise error from None
            result = ActivityResult(
                attempt_id=envelope.attempt_id,
                lease_revision=revision,
                result=None,
                code=failure_code(error),
                usage=zero(deps),
            )
        status = self.report(result)
        self.fenced()
        return status
