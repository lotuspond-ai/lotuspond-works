"""Credential/session lifecycle only. This supervisor cannot execute business work."""

from __future__ import annotations

import math
import secrets
import threading
import time

from .client import RuntimeClient
from .errors import FleetError
from .protocol import (
    AcquiredSession,
    CredentialState,
    PreparedCredential,
    RuntimeObservation,
    SelfState,
    validate_token,
)
from .state import CredentialStore


class RuntimeSupervisor:
    """Bind server responses to the independently provisioned installation_id."""

    def __init__(
        self,
        client: RuntimeClient,
        store: CredentialStore,
        observation: RuntimeObservation,
        *,
        installation_id: str,
        command_records=None,
        command_trust=None,
        wall_ms=lambda: time.time_ns() // 1000000,
        monotonic=time.monotonic,
        jitter_ms=lambda: secrets.randbelow(30001),
        wait=lambda stop, seconds: stop.wait(seconds),
        checkpoint=lambda point: None,
        activity_clock=None,
    ):
        from .commands import DenyRuntimeRecordSource, _RecordLookup
        from .trust import UnavailableCommandTrustAuthority

        self._command_records = command_records or DenyRuntimeRecordSource()
        self._command_trust = command_trust or UnavailableCommandTrustAuthority()
        self._command_lookup = _RecordLookup()
        self._pending_delivery = None
        self._poll_at = 0
        self.command_status = "waiting"
        self.client, self.store = client, store
        self.observation = RuntimeObservation.model_validate(observation)
        self.installation_id = installation_id
        self._wall, self._monotonic, self._jitter = wall_ms, monotonic, jitter_ms
        self._wait, self._checkpoint = wait, checkpoint
        self._session = None
        self._identity = None
        self._attempt = 0
        self._server_anchor = self._mono_anchor = self._wall_anchor = None
        # The activity worker's server-anchored clock (Task 12). Lease responses carry
        # no server time, and a worker can sit in one provider call for longer than
        # the clock's anchor age, so it is re-anchored from here, on this loop's own
        # transport, by every session and heartbeat response. Both use one monotonic
        # source, so `sent` and `received` mean the same instants to it.
        if activity_clock is not None and (
            not callable(getattr(activity_clock, "anchor", None))
            # Equal, not identical: each access to a bound method makes a new object.
            or getattr(activity_clock, "_monotonic", None) != monotonic
        ):
            raise ValueError("The activity clock must share the supervisor's monotonic source")
        self._activity_clock = activity_clock
        self._shared = None

    def activity_session(self):
        """The live (credential token, session secret, identity), or None.

        The activity worker builds its own transport from it (Task 13). Only a whole
        tuple is ever assigned, so another thread always reads a consistent one; it is
        replaced on rotation and cleared when this loop ends.
        """
        return self._shared

    def _remote(self, method, state, model, *args):
        started = self._monotonic()
        value = model.model_validate(getattr(self.client, method)(*args))
        finished = self._monotonic()
        elapsed = finished - started
        if not 0 <= elapsed <= 10 or abs(value.server_time_ms - self._wall()) > 30000:
            raise FleetError("authority_unavailable")
        identity = getattr(value, "identity", None)
        if identity is not None:
            if (
                identity.instance_id != state.instance_id
                or identity.installation_id != self.installation_id
            ):
                raise FleetError("forbidden")
            if self._identity is not None and (
                identity.scope != self._identity.scope
                or identity.pool_id != self._identity.pool_id
                or identity.recovery_epoch != self._identity.recovery_epoch
                or identity.generation != self._identity.generation
            ):
                raise FleetError("forbidden")
        if isinstance(value, PreparedCredential):
            validate_token(value.token, "lpr_")
        if isinstance(value, AcquiredSession):
            validate_token(value.secret, "lps_")
        # Full roundtrip is a conservative upper estimate of response age. Schedule
        # against confirmed server time plus elapsed monotonic time, never wall skew.
        self._server_anchor = value.server_time_ms + math.ceil(elapsed * 1000)
        self._mono_anchor, self._wall_anchor = finished, self._wall()
        if self._activity_clock is not None:
            # `anchor` replaces one tuple, so the worker's thread always reads a whole one.
            self._activity_clock.anchor(value.server_time_ms, sent=started, received=finished)
        return value

    def _now(self):
        elapsed = self._monotonic() - self._mono_anchor
        if elapsed < 0 or abs(self._wall() - (self._wall_anchor + elapsed * 1000)) > 30000:
            raise FleetError("authority_unavailable")
        return self._server_anchor + math.ceil(elapsed * 1000)

    def _replace(self, state, token, pending=None, pending_version=None):
        replacement = CredentialState(
            revision=state.revision + 1,
            instance_id=state.instance_id,
            active_token=token,
            pending_token=pending,
            pending_version=pending_version,
        )
        self.store.save(state.revision, replacement)
        return replacement

    def _recover(self, state, stop):
        active = None
        if state.pending_token is not None:
            try:
                # No proof is supplied. Only already-active P may succeed.
                active = self._remote("activate", state, SelfState, state.pending_token, None)
            except FleetError as error:
                if error.code not in ("unauthenticated", "conflict"):
                    raise
            else:
                if active.identity.credential_version != state.pending_version:
                    raise FleetError("forbidden")
                state = self._replace(state, state.pending_token)
        if active is None:
            active = self._remote("self_state", state, SelfState, state.active_token)
        # C/P remains durable until the server confirms ownership after lease expiry.
        while (
            active.session_expires_at_ms is not None
            and active.server_time_ms < active.session_expires_at_ms
        ):
            delay = max(0.001, (active.session_expires_at_ms - self._now()) / 1000)
            if self._wait(stop, delay):
                return state, None
            active = self._remote("self_state", state, SelfState, state.active_token)
        return state, active

    def _rotate(self, state, active, stop, *, startup_recovery=False):
        # Normal scheduling must retain the five-minute lead. One already-late
        # startup recovery may rotate a still-valid C after fresh session proof.
        if not startup_recovery and self._now() > active.credential_expires_at_ms - 300000:
            raise FleetError("authority_unavailable")
        self._attempt += 1
        attempt = self._attempt
        pending = self._remote(
            "prepare", state, PreparedCredential, state.active_token, self._session
        )
        self._checkpoint("prepare_received")
        if (
            stop.is_set()
            or attempt != self._attempt
            or pending.activation_deadline_ms <= self._now()
            or pending.credential_version <= active.identity.credential_version
            or pending.token == state.active_token
        ):
            raise FleetError("authority_unavailable")
        self._checkpoint("before_pending_save")
        state = self._replace(state, state.active_token, pending.token, pending.credential_version)
        self._checkpoint("after_pending_save")
        if stop.is_set() or self._now() >= pending.activation_deadline_ms:
            raise FleetError("authority_unavailable")
        self._checkpoint("before_activate")
        result = self._remote("activate", state, SelfState, state.pending_token, self._session)
        self._checkpoint("after_activate")
        if result.identity.credential_version != state.pending_version:
            raise FleetError("forbidden")
        self._checkpoint("before_final_save")
        state = self._replace(state, state.pending_token)
        self._checkpoint("after_final_save")
        return state, result

    def _commands(self, state, stop, valid_until_ms):
        from lotuspond_sdk.executable.wire import validate_runtime_command, wire_digest

        from .canonical import decode_fleet_json
        from .commands import validate_delivery
        from .protocol import AdmissionView

        try:
            started = self._monotonic()
            if stop.is_set() or self._now() >= valid_until_ms:
                raise FleetError("unauthenticated")
            if self._pending_delivery is None:
                value = self.client.poll(state.active_token, self._session)
                if (
                    not 0 <= self._monotonic() - started <= 10
                    or stop.is_set()
                    or self._now() >= valid_until_ms
                ):
                    raise FleetError("authority_unavailable")
                self._pending_delivery = value
                self._poll_at = self._monotonic() + 1
                self.command_status = "waiting"
                return
            delivery = self._pending_delivery
            command = validate_runtime_command(decode_fleet_json(delivery.command_bytes))
            pins, trust = self._command_lookup.load(
                self._command_records, self._command_trust, command, self.installation_id
            )
            if (
                not 0 <= self._monotonic() - started <= 5
                or stop.is_set()
                or self._now() >= valid_until_ms
            ):
                raise FleetError("authority_unavailable")
            command = validate_delivery(
                delivery, identity=self._identity, records=pins, trust=trust, now_ms=self._now()
            )
            if stop.is_set() or self._now() >= valid_until_ms:
                raise FleetError("authority_unavailable")
            claimed = AdmissionView.model_validate(
                self.client.claim(
                    state.active_token, self._session, command.command_id, wire_digest(command)
                )
            )
            if (
                not 0 <= self._monotonic() - started <= 15
                or stop.is_set()
                or self._now() >= valid_until_ms
            ):
                raise FleetError("authority_unavailable")
            # Admission is closed only. There is deliberately no invocation path.
            validate_delivery(
                delivery, identity=self._identity, records=pins, trust=trust, now_ms=self._now()
            )
            if (
                claimed.command_id != command.command_id
                or claimed.command_sha256 != wire_digest(command)
                or claimed.status != "claimed"
            ):
                raise FleetError("command_rejected")
            self._pending_delivery = None
            self.command_status = "claimed"
        except Exception:
            self._pending_delivery = None
            self._poll_at = self._monotonic() + 1
            self.command_status = "denied"

    def _next_heartbeat(self, rotation_at, lease_expires):
        now = self._monotonic()
        rotation_deadline = now + (rotation_at - self._now()) / 1000
        deadline = now + 20
        # A ten-second heartbeat can advance the conservative server estimate
        # by twenty seconds when its timestamp is sampled at completion. Keep
        # one further second for loop/clock granularity before the target.
        headroom_deadline = rotation_deadline - 21
        if headroom_deadline > now:
            deadline = min(deadline, headroom_deadline)
        elif deadline <= rotation_deadline:
            # Do not launch another heartbeat inside the reserved window. A
            # deferred heartbeat must still fit after prepare + activate (20s)
            # and its own full attempt (10s), within the current half-open lease.
            # If that lease would be too short, renew now while the normal
            # interval leaves at least twenty seconds before rotation.
            deadline = now if lease_expires <= rotation_at + 30000 else rotation_deadline
        return deadline

    def _run(self, stop):
        if stop.is_set():
            return
        state = self.store.load()
        state, active = self._recover(state, stop)
        if active is None or stop.is_set():
            return
        session = self._remote("acquire", state, AcquiredSession, state.active_token)
        if (
            session.identity.credential_version != active.identity.credential_version
            or session.identity.scope != active.identity.scope
            or session.identity.pool_id != active.identity.pool_id
            or session.identity.recovery_epoch != active.identity.recovery_epoch
            or session.identity.generation < active.identity.generation
        ):
            raise FleetError("forbidden")
        # Acquisition advances process generation. Retain that validated identity
        # in the working self-state too, so polling cannot restore the earlier
        # pre-acquisition generation. Credential expiry is never extended here.
        active = SelfState(
            identity=session.identity,
            credential_expires_at_ms=active.credential_expires_at_ms,
            session_expires_at_ms=session.expires_at_ms,
            server_time_ms=session.server_time_ms,
        )
        self._session, self._identity = session.secret, session.identity
        if state.pending_token is not None:
            state = self._replace(state, state.active_token)
        self._shared = (state.active_token, session.secret, session.identity)
        jitter = self._jitter()
        if type(jitter) is not int or not 0 <= jitter <= 30000:
            raise FleetError("invalid_request")
        next_rotation = active.credential_expires_at_ms - 300000 - jitter
        startup_overdue = self._now() > next_rotation
        lease_expires = session.expires_at_ms
        heartbeat_at = self._next_heartbeat(next_rotation, lease_expires)
        while not stop.is_set():
            now = self._now()
            if now >= active.credential_expires_at_ms or now >= lease_expires:
                raise FleetError("unauthenticated")
            if self._now() >= next_rotation and not stop.is_set():
                state, active = self._rotate(state, active, stop, startup_recovery=startup_overdue)
                self._shared = (state.active_token, self._session, self._identity)
                startup_overdue = False
                next_rotation = active.credential_expires_at_ms - 300000 - jitter
                if next_rotation <= self._now():
                    raise FleetError("authority_unavailable")
            if self._monotonic() >= heartbeat_at:
                previous_version = active.identity.credential_version
                active = self._remote(
                    "heartbeat", state, SelfState, state.active_token, self._session
                )
                if (
                    active.session_expires_at_ms is None
                    or active.identity.credential_version != previous_version
                ):
                    raise FleetError("unauthenticated")
                lease_expires = active.session_expires_at_ms
                heartbeat_at = self._next_heartbeat(next_rotation, lease_expires)
                # A completed heartbeat may land exactly on the rotation target.
                # Re-evaluate immediately instead of adding a forced wait.
                continue
            # Poll (10s) and fresh record/trust+claim (5s+10s) are separate
            # stages. Reserve each full budget before heartbeat/rotation targets;
            # timestamps from command responses never advance the clock anchor.
            budget = 16 if self._pending_delivery is not None else 11
            if (
                self._monotonic() >= self._poll_at
                and heartbeat_at - self._monotonic() > budget
                and next_rotation - self._now() > budget * 1000
                and min(lease_expires, active.credential_expires_at_ms) - self._now()
                > budget * 1000
            ):
                self._identity = active.identity
                self._commands(
                    state, stop, min(lease_expires, active.credential_expires_at_ms, next_rotation)
                )
                continue
            delay = min(
                1.0,
                max(0.001, heartbeat_at - self._monotonic()),
                max(0.001, (next_rotation - self._now()) / 1000),
            )
            self._wait(stop, delay)

    def run(self, stop: threading.Event) -> int:
        result = 0
        try:
            self._run(stop)
        except FleetError as error:
            result = 4 if error.code == "unsafe_storage" else 3
        finally:
            self._shared = None
            self._session = None
            self._identity = None
            self._pending_delivery = None
            self._attempt += 1
            try:
                self.client.close()
            except FleetError:
                result = 3
        return result
