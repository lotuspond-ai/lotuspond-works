"""Action projections (E05 Task 10): read models over committed action rows.

Read without row locks, as E04's projections are. An `ActionView` carries the
intent's own authorization, effect and outcome columns and nothing inferred: the
outcome stays `unknown` until a verification recorded `verified` (Task 9), and a
receipt is shown as the runtime reported it, never as success. Views carry digests
and identifiers only, never a proposal's values.
"""

from lotuspond_runtime.protocol import CanonicalUUID
from pydantic import TypeAdapter

from ..models import Execution, ExecutionActionIntent
from .action_contracts import ActionView
from .action_reconciliation import action_view
from .authorization import reauthorize, record, scoped
from .errors import ExecutionError, execution_service

MAX_PAGE = 100


class ActionProjection:
    """`list` and `get` the actions of one execution the actor may read."""

    @execution_service
    def list(self, db, actor, execution_id: str, *, after_id=None, limit=50) -> dict:
        """One page by intent ID: `{"items": [ActionView], "next": str | None}`."""
        actor, execution = self._execution(db, actor, execution_id)
        if type(limit) is not int or not 1 <= limit <= MAX_PAGE:
            raise ExecutionError("invalid_request")
        query = scoped(ExecutionActionIntent, actor.scope).where(
            ExecutionActionIntent.execution_id == execution.id
        )
        if after_id is not None:
            after_id = TypeAdapter(CanonicalUUID).validate_python(after_id, strict=True)
            if db.scalar(query.where(ExecutionActionIntent.id == after_id)) is None:
                raise ExecutionError("not_found")
            query = query.where(ExecutionActionIntent.id > after_id)
        rows = list(db.scalars(query.order_by(ExecutionActionIntent.id).limit(limit + 1)))
        items = [action_view(db, actor.scope, row) for row in rows[:limit]]
        return {"items": items, "next": rows[limit - 1].id if len(rows) > limit else None}

    @execution_service
    def get(self, db, actor, execution_id: str, action_id: str) -> ActionView:
        actor, execution = self._execution(db, actor, execution_id)
        action_id = TypeAdapter(CanonicalUUID).validate_python(action_id, strict=True)
        intent = db.scalar(
            scoped(ExecutionActionIntent, actor.scope).where(
                ExecutionActionIntent.execution_id == execution.id,
                ExecutionActionIntent.action_id == action_id,
            )
        )
        if intent is None:
            raise ExecutionError("not_found")
        return action_view(db, actor.scope, intent)

    @staticmethod
    def _execution(db, actor, execution_id):
        actor = reauthorize(db, actor, "read")
        execution_id = TypeAdapter(CanonicalUUID).validate_python(execution_id, strict=True)
        return actor, record(db, Execution, actor.scope, execution_id)
