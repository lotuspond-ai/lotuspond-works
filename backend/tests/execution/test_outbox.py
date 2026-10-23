import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from lotuspond.execution.errors import ExecutionError
from lotuspond.execution.outbox import ack_outbox, lease_outbox
from lotuspond.fleet.authority import database_now_ms
from lotuspond.models import Execution, ExecutionOutbox

from .test_admission import admission as admission
from .test_admission import admit
from .test_budgets import budget_harness as budget_harness
from .test_publication import harness as harness


def test_lost_ack_releases_only_matching_outbox_lease(admission):
    h = admission
    execution = admit(h)
    with Session(h.engine) as db, db.begin():
        (first,) = lease_outbox(db, "owner-1")
        assert first.kind == "start" and first.opaque_token == execution.id
    with Session(h.engine) as db, db.begin():
        assert lease_outbox(db, "owner-2") == ()
        row = db.get(ExecutionOutbox, first.id)
        row.leased_at_ms = database_now_ms(db) - 31000
        row.lease_expires_at_ms = database_now_ms(db) - 1000
    with Session(h.engine) as db, db.begin():
        (second,) = lease_outbox(db, "owner-2")
        assert second.id == first.id and second.lease_revision == first.lease_revision + 1
    for revision, digest in (
        (first.lease_revision, first.digest),
        (second.lease_revision, "a" * 64),
    ):
        with Session(h.engine) as db, pytest.raises(ExecutionError), db.begin():
            ack_outbox(db, first.id, revision, digest)
    with Session(h.engine) as db, db.begin():
        assert db.get(ExecutionOutbox, first.id).status == "leased"
        ack_outbox(db, second.id, second.lease_revision, second.digest)
    with Session(h.engine) as db:
        assert db.get(ExecutionOutbox, first.id).status == "delivered"


def test_outbox_skip_locked_deadline_and_batch_bounds(admission):
    h = admission
    execution = admit(h)
    with Session(h.engine) as blocker, blocker.begin():
        blocker.scalar(select(ExecutionOutbox).with_for_update())
        with Session(h.engine) as db, db.begin():
            assert lease_outbox(db, "owner") == ()
    for count in (0, 26, True):
        with Session(h.engine) as db, pytest.raises(ExecutionError), db.begin():
            lease_outbox(db, "owner", count)
    with Session(h.engine) as db, db.begin():
        row = db.get(Execution, execution.id)
        row.admitted_at_ms = database_now_ms(db) - 2000
        row.deadline_ms = database_now_ms(db) - 1000
    with Session(h.engine) as db, db.begin():
        assert lease_outbox(db, "owner") == ()
        assert db.scalar(select(ExecutionOutbox.status)) == "pending"


def test_lease_and_ack_rollback_do_not_lose_outbox(admission):
    h = admission
    admit(h)
    with Session(h.engine) as db, pytest.raises(RuntimeError), db.begin():
        lease_outbox(db, "owner")
        raise RuntimeError("injected before lease commit")
    with Session(h.engine) as db, db.begin():
        assert db.scalar(select(ExecutionOutbox.status)) == "pending"
        (item,) = lease_outbox(db, "owner")
        assert item.lease_revision == 1
    with Session(h.engine) as db, pytest.raises(RuntimeError), db.begin():
        ack_outbox(db, item.id, item.lease_revision, item.digest)
        raise RuntimeError("injected before ack commit")
    with Session(h.engine) as db, db.begin():
        assert db.scalar(select(ExecutionOutbox.status)) == "leased"
        ack_outbox(db, item.id, item.lease_revision, item.digest)
        ack_outbox(db, item.id, item.lease_revision, item.digest)


def test_outbox_lease_is_clipped_to_original_deadline(admission):
    from .test_admission import wait_for_database_time

    h = admission
    execution = admit(h)
    with Session(h.engine) as db, db.begin():
        deadline = database_now_ms(db) + 1000
        db.get(Execution, execution.id).deadline_ms = deadline
    with Session(h.engine) as db, db.begin():
        (item,) = lease_outbox(db, "owner")
        row = db.get(ExecutionOutbox, item.id)
        assert row.lease_expires_at_ms == deadline
        assert row.lease_expires_at_ms - row.leased_at_ms <= 30000
    wait_for_database_time(h, deadline)
    with Session(h.engine) as db, pytest.raises(ExecutionError, match="conflict"), db.begin():
        ack_outbox(db, item.id, item.lease_revision, item.digest)
    with Session(h.engine) as db, db.begin():
        assert lease_outbox(db, "owner") == ()
