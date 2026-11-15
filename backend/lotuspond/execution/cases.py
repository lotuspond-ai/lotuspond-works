"""Cases and exception ownership (E06, second part).

docs/superpowers/specs/2026-11-15-cases-design.md. A case is a business item in one
environment, named by its key. The key is customer data: it is stored encrypted as an
execution artifact, and only that artifact's digest names the case in plain text.
Executions join a case explicitly. The case's outcome is its latest execution's business
outcome; it needs a person while an execution admitted after its last resolution is an
exception. A person takes ownership and resolves it with a closed kind and no free text.
A resolution changes no execution and sends nothing.
"""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Literal

from lotuspond_runtime.protocol import CanonicalUUID, OpaqueId
from pydantic import Field, TypeAdapter
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..fleet.authority import database_now_ms
from ..models import (
    CASE_RESOLUTIONS,
    Execution,
    ExecutionActionIntent,
    ExecutionCase,
    ExecutionCaseLink,
    User,
)
from .artifacts import ArtifactPolicy
from .authorization import mark_replay, reauthorize, record, scoped
from .contracts import Contract
from .errors import ExecutionError, execution_service
from .outcomes import business_outcome

MAX_EXECUTIONS = 100
MAX_PAGE = 100


class CaseRequest(Contract):
    key: str = Field(min_length=1, max_length=200)


class CaseLinkRequest(Contract):
    execution_id: CanonicalUUID


class CaseOwnerRequest(Contract):
    request_id: OpaqueId
    action: Literal["take", "release"]


class CaseResolutionRequest(Contract):
    request_id: OpaqueId
    kind: Literal[CASE_RESOLUTIONS]  # type: ignore[valid-type]


def key_content(key: str) -> bytes:
    """The key artifact's exact bytes; their SHA-256 names the case."""
    return json.dumps({"key": key}, ensure_ascii=False, separators=(",", ":")).encode()


def key_digest(key: str) -> str:
    return sha256(key_content(key)).hexdigest()


def _person(db, user_id):
    if user_id is None:
        return None
    user = db.get(User, user_id)
    return {"user_id": user_id, "name": None if user is None else user.name}


def _uuid(value):
    return TypeAdapter(CanonicalUUID).validate_python(value, strict=True)


class CaseService:
    """Create, read, link, own and resolve the cases of one environment."""

    def __init__(self, *, artifacts, artifact_policy):
        if artifacts is None:
            raise ValueError("An artifact store is required")
        self.artifacts = artifacts
        self.policy = ArtifactPolicy.model_validate(artifact_policy)

    # ------------------------------------------------------------------ writes

    @execution_service
    def create(self, db, actor, request) -> dict:
        """The case named by this key, created if it does not exist yet."""
        request = CaseRequest.model_validate(request)
        actor = reauthorize(db, actor, "execute")
        digest = key_digest(request.key)
        existing = self._by_digest(db, actor.scope, digest)
        if existing is not None:
            mark_replay(db)
            return self._view(db, actor, existing, executions=False)
        handle = self.artifacts.put(db, actor, key_content(request.key), self.policy)
        row = ExecutionCase(
            **actor.scope.model_dump(),
            key_artifact_id=handle.id,
            key_artifact_version=handle.version,
            key_sha256=handle.sha256,
            created_by_user_id=actor.user_id,
        )
        try:
            with db.begin_nested():
                db.add(row)
                db.flush()
        except IntegrityError:
            # Another request created it first: answer with that case.
            existing = self._by_digest(db, actor.scope, digest)
            if existing is None:
                raise ExecutionError("conflict") from None
            mark_replay(db)
            return self._view(db, actor, existing, executions=False)
        return self._view(db, actor, row, executions=False)

    @execution_service
    def link(self, db, actor, case_id, request) -> dict:
        request = CaseLinkRequest.model_validate(request)
        actor = reauthorize(db, actor, "execute")
        case = record(db, ExecutionCase, actor.scope, _uuid(case_id), locked=True)
        execution = record(db, Execution, actor.scope, request.execution_id)
        existing = db.scalar(
            scoped(ExecutionCaseLink, actor.scope).where(
                ExecutionCaseLink.execution_id == execution.id
            )
        )
        if existing is not None:
            if existing.case_id != case.id:
                raise ExecutionError("conflict")  # an execution belongs to one case
            mark_replay(db)
        else:
            db.add(
                ExecutionCaseLink(
                    **actor.scope.model_dump(),
                    case_id=case.id,
                    execution_id=execution.id,
                    linked_by_user_id=actor.user_id,
                    linked_at_ms=database_now_ms(db),
                )
            )
            db.flush()
        return self._view(db, actor, case)

    @execution_service
    def owner(self, db, actor, case_id, request) -> dict:
        request = CaseOwnerRequest.model_validate(request)
        actor = self._person(db, actor)
        case = record(db, ExecutionCase, actor.scope, _uuid(case_id), locked=True)
        if case.owner_request_id == request.request_id:
            mine = case.owner_user_id == actor.user_id
            if (request.action == "take") == mine:
                mark_replay(db)
                return self._view(db, actor, case)
            raise ExecutionError("conflict")
        if request.action == "take":
            if case.owner_user_id not in (None, actor.user_id):
                raise ExecutionError("conflict")  # someone else holds it
            if case.owner_user_id is None:
                case.owner_user_id = actor.user_id
                case.owner_since_ms = database_now_ms(db)
        else:
            if case.owner_user_id is None:
                mark_replay(db)
                return self._view(db, actor, case)
            if case.owner_user_id != actor.user_id and actor.role not in {"owner", "admin"}:
                raise ExecutionError("forbidden")
            case.owner_user_id = case.owner_since_ms = None
        case.owner_request_id = request.request_id
        db.flush()
        return self._view(db, actor, case)

    @execution_service
    def resolve(self, db, actor, case_id, request) -> dict:
        request = CaseResolutionRequest.model_validate(request)
        actor = self._person(db, actor)
        case = record(db, ExecutionCase, actor.scope, _uuid(case_id), locked=True)
        if case.resolution_request_id == request.request_id:
            if case.resolution == request.kind:
                mark_replay(db)
                return self._view(db, actor, case)
            raise ExecutionError("conflict")
        if case.owner_user_id != actor.user_id:
            raise ExecutionError("forbidden")  # only the case's owner resolves it
        executions = self._executions(db, actor.scope, case)
        if not self._exception_open(case, executions):
            raise ExecutionError("conflict")  # nothing needs a person
        now = database_now_ms(db)
        case.resolution = request.kind
        case.resolution_request_id = request.request_id
        case.resolved_by_user_id = actor.user_id
        case.resolved_at_ms = now
        case.resolved_through_ms = max([now, *(row["admitted_at_ms"] for row in executions)])
        db.flush()
        return self._view(db, actor, case)

    # ------------------------------------------------------------------ reads

    @execution_service
    def get(self, db, actor, case_id) -> dict:
        actor = reauthorize(db, actor, "read")
        return self._view(db, actor, record(db, ExecutionCase, actor.scope, _uuid(case_id)))

    @execution_service
    def list(self, db, actor, *, after_id=None, limit=50) -> dict:
        actor = reauthorize(db, actor, "read")
        if type(limit) is not int or not 1 <= limit <= MAX_PAGE:
            raise ExecutionError("invalid_request")
        query = scoped(ExecutionCase, actor.scope)
        if after_id is not None:
            after_id = _uuid(after_id)
            if db.scalar(query.where(ExecutionCase.id == after_id)) is None:
                raise ExecutionError("not_found")
            query = query.where(ExecutionCase.id > after_id)
        rows = list(db.scalars(query.order_by(ExecutionCase.id).limit(limit + 1)))
        return {
            "items": [self._view(db, actor, row, executions=False) for row in rows[:limit]],
            "next_after_id": rows[limit - 1].id if len(rows) > limit else None,
        }

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _person(db, actor):
        """A person's session with `review`: ownership and resolutions are people's."""
        if actor.api_key_id is not None or actor.user_id is None:
            raise ExecutionError("forbidden")
        return reauthorize(db, actor, "review")

    @staticmethod
    def _by_digest(db, scope, digest):
        return db.scalar(scoped(ExecutionCase, scope).where(ExecutionCase.key_sha256 == digest))

    @staticmethod
    def _executions(db, scope, case) -> list[dict]:
        """The case's executions, newest first, each with its business outcome."""
        rows = db.execute(
            select(Execution.id, Execution.state, Execution.admitted_at_ms)
            .join(
                ExecutionCaseLink,
                (ExecutionCaseLink.workspace_id == Execution.workspace_id)
                & (ExecutionCaseLink.project_id == Execution.project_id)
                & (ExecutionCaseLink.environment_id == Execution.environment_id)
                & (ExecutionCaseLink.execution_id == Execution.id),
            )
            .where(
                ExecutionCaseLink.workspace_id == scope.workspace_id,
                ExecutionCaseLink.project_id == scope.project_id,
                ExecutionCaseLink.environment_id == scope.environment_id,
                ExecutionCaseLink.case_id == case.id,
            )
            .order_by(Execution.admitted_at_ms.desc(), Execution.id.desc())
            .limit(MAX_EXECUTIONS)
        ).all()
        effects: dict[str, list] = {row.id: [] for row in rows}
        if effects:
            for execution_id, effect, outcome in db.execute(
                scoped(ExecutionActionIntent, scope)
                .where(ExecutionActionIntent.execution_id.in_(list(effects)))
                .with_only_columns(
                    ExecutionActionIntent.execution_id,
                    ExecutionActionIntent.effect_state,
                    ExecutionActionIntent.outcome_state,
                )
            ).all():
                effects[execution_id].append((effect, outcome))
        views = []
        for row in rows:
            outcome, exception = business_outcome(row.state, effects[row.id])
            views.append(
                {
                    "id": row.id,
                    "state": row.state,
                    "admitted_at_ms": row.admitted_at_ms,
                    "outcome": outcome,
                    "exception": exception,
                }
            )
        return views

    @staticmethod
    def _exception_open(case, executions) -> bool:
        through = case.resolved_through_ms
        return any(
            row["exception"] and (through is None or row["admitted_at_ms"] > through)
            for row in executions
        )

    def _key(self, db, actor, case):
        try:
            content = self.artifacts.get(
                db,
                actor,
                {
                    "id": case.key_artifact_id,
                    "version": case.key_artifact_version,
                    "sha256": case.key_sha256,
                },
            )
            return json.loads(content)["key"]
        except ExecutionError as error:
            if error.code in {"key_unavailable", "authority_unavailable"}:
                raise
            return None  # past retention or unreadable: the case stays, its key does not

    def _view(self, db, actor, case, *, executions=True) -> dict:
        rows = self._executions(db, actor.scope, case)
        latest = rows[0]["outcome"] if rows else "empty"
        open_exception = self._exception_open(case, rows)
        view = {
            "id": case.id,
            "key": self._key(db, actor, case),
            "key_sha256": case.key_sha256,
            "created_at": case.created_at.isoformat(),
            "created_by": _person(db, case.created_by_user_id),
            "outcome": latest,
            "needs_person": open_exception,
            "owner": None
            if case.owner_user_id is None
            else {**_person(db, case.owner_user_id), "since_ms": case.owner_since_ms},
            "resolution": None
            if case.resolution is None
            else {
                "kind": case.resolution,
                "resolved_by": _person(db, case.resolved_by_user_id),
                "resolved_at_ms": case.resolved_at_ms,
                "through_ms": case.resolved_through_ms,
            },
            "execution_count": len(rows),
        }
        if executions:
            view["executions"] = rows
        return view
