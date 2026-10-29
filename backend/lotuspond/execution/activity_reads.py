"""What a runtime may read about its own work (ADR-0011, Task 12).

Each read is linked to the caller's own session: the pinned records of a delivery
dispatched to it, or the declarations, input and pinned context of an attempt it
holds under a live lease. There is no generic artifact capability. Rows are
locked in Task 5's order through `ActivityService._locked`.

A delivery this session will not be served gets `forbidden`, `not_found` or
`conflict`; an outage gets an `*_unavailable` code. Task 10's runtime declines only
the former (the decline contract). Records and input grant nothing, so they need
the runtime's current identity and session but not the initiator's current
`execute`; context is read under the initiator's current access to the source.
"""

from lotuspond_runtime.activity_protocol import PinnedActivityRecords
from lotuspond_sdk.activity import ArtifactHandle
from lotuspond_sdk.executable.package import AgentStep

from ..models import ExecutionActivityDelivery
from .activity_records import scope_of
from .authorization import record
from .context import ContextRequest
from .contracts import DeploymentConfig, canonical_value
from .dispatch import activity_service
from .errors import ExecutionError
from .program import ProgramError, load_program, resolve_occurrence


class ActivityReads:
    def __init__(self, activities, context):
        if activities is None or context is None:
            raise ValueError("Activity and context services are required")
        self.activities, self.sources = activities, context

    def _delivery(self, db, principal, delivery_id, digest, *, authority):
        """This session's delivery, named by its exact digest, with its rows locked."""
        a = self.activities
        identity = principal.identity
        hint = record(db, ExecutionActivityDelivery, identity.scope, delivery_id)
        rows = a._locked(db, principal, hint.attempt_id, authority=authority)
        delivery = rows["deliveries"].get(rows["attempt"].id)
        if (
            delivery is None
            or delivery.id != hint.id
            or delivery.pool_id != identity.pool_id
            or delivery.instance_id != identity.instance_id
            or delivery.generation != identity.generation
            or delivery.session_id != principal.session_id
        ):
            raise ExecutionError("forbidden")
        if delivery.digest != digest:
            raise ExecutionError("conflict")
        return rows, delivery, a._finish(db, rows["locked"])

    def _held(self, db, principal, attempt_id, *, authority):
        """An attempt this session holds under a live lease, with its rows locked."""
        a = self.activities
        rows = a._locked(db, principal, attempt_id, authority=authority)
        now = a._finish(db, rows["locked"])
        a._held(rows, rows["attempt"], principal, now)
        return rows

    @activity_service
    def records(self, db, principal, delivery_id: str, digest: str) -> PinnedActivityRecords:
        """The pinned records of a delivery dispatched to this session, claimed or not."""
        rows, delivery, now = self._delivery(db, principal, delivery_id, digest, authority=False)
        attempt = rows["attempt"]
        if delivery.status == "leased":
            self.activities._held(rows, attempt, principal, now)
        elif delivery.status != "pending" or now >= delivery.expires_at_ms:
            raise ExecutionError("conflict")
        return self.activities.records.load(db, attempt.id)

    def _agent(self, db, rows):
        """The claimed root agent attempt's step, its package and its pins."""
        execution, step, attempt = rows["execution"], rows["step"], rows["attempt"]
        if attempt.parent_attempt_id is not None:
            raise ExecutionError("forbidden")  # a child is no agent
        try:
            ref = resolve_occurrence(
                load_program(db, scope_of(execution), execution), step.occurrence_path
            )
        except ProgramError:
            raise ExecutionError("dependency_unavailable") from None
        if not isinstance(ref.step, AgentStep):
            raise ExecutionError("forbidden")
        pins = self.activities.records.pins(db, execution, step, attempt)
        return ref, pins

    @staticmethod
    def _knowledge(rows, ref):
        """Each knowledge reference the step pinned, with its deployment-bound version."""
        configuration = DeploymentConfig.model_validate_json(
            rows["revision"].canonical_configuration
        )
        declared = {item.id: item for item in ref.package.knowledge}
        found = []
        for name in ref.step.knowledge_refs:
            declaration = declared.get(name)
            wanted = None if declaration is None else canonical_value(declaration.ref.model_dump())
            binding = next(
                (
                    b
                    for b in configuration.bindings
                    if canonical_value(b.dependency.model_dump()) == wanted
                ),
                None,
            )
            if binding is None or binding.source_id is None or binding.source_version is None:
                raise ExecutionError("dependency_unavailable")
            found.append((binding.source_id, binding.source_version, declaration))
        return found

    @activity_service
    def agent(self, db, principal, delivery_id: str, digest: str) -> dict:
        """The pinned declarations of a root agent attempt this session holds."""
        rows, delivery, now = self._delivery(db, principal, delivery_id, digest, authority=False)
        if delivery.status != "leased":
            raise ExecutionError("conflict")
        self.activities._held(rows, rows["attempt"], principal, now)
        ref, pins = self._agent(db, rows)
        model = next(item for item in pins.declarations if item.kind == "model")
        limits = ref.step.limits
        return {
            "model": model.dependency.model_dump(mode="json"),
            "model_name": model.name,
            "instructions": ref.step.instructions,
            "tools": [
                {
                    "name": item.name,
                    "dependency": item.dependency.model_dump(mode="json"),
                    "operation": item.operation,
                    "input_schema": item.input_schema,
                    "output_schema": item.output_schema,
                }
                for item in pins.declarations
                if item.kind == "connector_read"
            ],
            "context": [
                {"source_id": source_id, "version": version}
                for source_id, version, _ in self._knowledge(rows, ref)
            ],
            "max_steps": limits.max_steps,
            "max_input_tokens": limits.max_input_tokens,
            "max_output_tokens": limits.max_output_tokens,
            "max_cost_microusd": limits.max_cost_microusd,
        }

    @activity_service
    def input(self, db, principal, attempt_id: str, handle) -> bytes:
        """The exact input of an attempt this session holds; no other artifact."""
        handle = ArtifactHandle.model_validate(handle)
        rows = self._held(db, principal, attempt_id, authority=False)
        attempt = rows["attempt"]
        if (handle.id, handle.version, handle.sha256) != (
            attempt.input_artifact_id,
            attempt.input_artifact_version,
            attempt.input_sha256,
        ):
            raise ExecutionError("forbidden")
        return self.activities.artifacts._get_internal(db, scope_of(attempt), handle)

    @activity_service
    def context(self, db, principal, attempt_id: str, source_id: str, version: int) -> bytes:
        """One knowledge source the held agent step pinned, under current source access.

        Resolved for the execution's initiator with the declaration's freshness and
        classifications, so a revoked source, lost access or a stale version refuses.
        """
        rows = self._held(db, principal, attempt_id, authority=True)
        ref, _ = self._agent(db, rows)
        match = [
            declaration
            for pinned_source, pinned_version, declaration in self._knowledge(rows, ref)
            if (pinned_source, pinned_version) == (source_id, version)
        ]
        if len(match) != 1:
            raise ExecutionError("forbidden")
        declaration, initiator = match[0], rows["actor"]
        resolved = self.sources.resolve(
            db,
            initiator,
            ContextRequest(
                source_id=source_id,
                version=version,
                required_freshness_seconds=declaration.required_freshness_seconds,
                allowed_classifications=tuple(declaration.allowed_classifications),
            ),
        )
        return self.sources.artifacts.get(db, initiator, resolved.value)
