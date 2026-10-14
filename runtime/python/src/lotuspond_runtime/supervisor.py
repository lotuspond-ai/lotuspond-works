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
        wall_ms=lambda: time.time_ns() // 1000000,
        monotonic=time.monotonic,
        jitter_ms=lambda: secrets.randbelow(30001),
        wait=lambda stop, seconds: stop.wait(seconds),
        checkpoint=lambda point: None,
    ):
        self.client, self.store = client, store
        self.observation = RuntimeObservation.model_validate(observation)
        self.installation_id = installation_id
        self._wall, self._monotonic, self._jitter = wall_ms, monotonic, jitter_ms
        self._wait, self._checkpoint = wait, checkpoint
        self._session = None
        self._identity = None
        self._attempt = 0
        self._server_anchor = self._mono_anchor = self._wall_anchor = None

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
        self._session, self._identity = session.secret, session.identity
        if state.pending_token is not None:
            state = self._replace(state, state.active_token)
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
            self._session = None
            self._identity = None
            self._attempt += 1
            try:
                self.client.close()
            except FleetError:
                result = 3
        return result
