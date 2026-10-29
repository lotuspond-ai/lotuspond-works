"""Leased delivery for external consumers, never customer or worker authority.

The PostgreSQL engine never calls these: its orchestrator consumes start, signal
and control rows in the transaction that applies them (ADR-0011). This two-step
protocol remains for an external consumer or a replacement engine adapter.
Commit leases before sending. A matching consumer acknowledgement is required in
a new caller-owned transaction; exceptions must roll back. A token is only a
lookup key: consumers reconstruct exact scoped records before acting on it.
"""

from hashlib import sha256
from typing import Literal

from lotuspond_runtime.protocol import CanonicalUUID, Digest, OpaqueId
from pydantic import Field, TypeAdapter
from sqlalchemy import and_, or_, select

from ..fleet.authority import database_now_ms
from ..models import Execution, ExecutionOutbox
from .contracts import Contract
from .errors import ExecutionError, execution_service


class OutboxItem(Contract):
    id: CanonicalUUID
    lease_revision: int = Field(ge=1, le=2147483647)
    kind: Literal["start", "signal", "control"]
    opaque_token: CanonicalUUID
    digest: Digest


KINDS = {"engine_start": "start", "engine_signal": "signal", "control": "control"}


@execution_service
def lease_outbox(db, owner_id: str, limit: int = 25) -> tuple[OutboxItem, ...]:
    owner_id = TypeAdapter(OpaqueId).validate_python(owner_id, strict=True)
    if type(limit) is not int or not 1 <= limit <= 25:
        raise ExecutionError("invalid_request")
    now = database_now_ms(db)
    query = (
        select(ExecutionOutbox, Execution.deadline_ms)
        .join(
            Execution,
            and_(
                Execution.id == ExecutionOutbox.execution_id,
                *(
                    getattr(Execution, k) == getattr(ExecutionOutbox, k)
                    for k in ("workspace_id", "project_id", "environment_id")
                ),
            ),
        )
        .where(
            ExecutionOutbox.kind.in_(KINDS),
            Execution.deadline_ms > now,
            ExecutionOutbox.available_at_ms <= now,
            ExecutionOutbox.delivery_count < 2147483647,
            or_(
                ExecutionOutbox.status == "pending",
                and_(
                    ExecutionOutbox.status == "leased", ExecutionOutbox.lease_expires_at_ms <= now
                ),
            ),
            or_(
                ExecutionOutbox.kind == "control",
                and_(
                    Execution.requested_state == "running",
                    Execution.state.in_(("pending", "running", "waiting")),
                ),
            ),
        )
        .order_by(ExecutionOutbox.available_at_ms, ExecutionOutbox.id)
        .limit(limit)
        .with_for_update(of=ExecutionOutbox, skip_locked=True)
        .execution_options(populate_existing=True)
    )
    result = []
    for row, deadline in db.execute(query):
        now = database_now_ms(db)
        if deadline <= now:
            continue
        row.status, row.leased_at_ms = "leased", now
        row.lease_expires_at_ms = min(now + 30000, deadline)
        row.delivery_count += 1
        row.lease_token_digest = sha256(
            f"{owner_id}\x00{row.id}\x00{row.delivery_count}".encode()
        ).hexdigest()
        result.append(
            OutboxItem(
                id=row.id,
                lease_revision=row.delivery_count,
                kind=KINDS[row.kind],
                opaque_token=row.message_id,
                digest=row.digest,
            )
        )
    db.flush()
    return tuple(result)


@execution_service
def ack_outbox(db, item_id: str, lease_revision: int, acknowledgement_digest: str) -> None:
    item_id = TypeAdapter(CanonicalUUID).validate_python(item_id, strict=True)
    acknowledgement_digest = TypeAdapter(Digest).validate_python(
        acknowledgement_digest, strict=True
    )
    if type(lease_revision) is not int or not 1 <= lease_revision <= 2147483647:
        raise ExecutionError("invalid_request")
    row = db.scalar(
        select(ExecutionOutbox)
        .where(ExecutionOutbox.id == item_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise ExecutionError("not_found")
    if (
        row.kind not in KINDS
        or row.delivery_count != lease_revision
        or row.digest != acknowledgement_digest
    ):
        raise ExecutionError("conflict")
    if row.status == "delivered":
        return
    now = database_now_ms(db)
    if row.status != "leased" or row.lease_expires_at_ms <= now:
        raise ExecutionError("conflict")
    row.status = "delivered"
    db.flush()
