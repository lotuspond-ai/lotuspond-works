import pytest

from tests.fleet.command_fixtures import admit, principal  # noqa: F401
from tests.fleet.identity_fixtures import auth, race_runtime


def test_replay_concurrency_and_conflicts(command_env):
    c = command_env
    if c.env.db.kw["bind"].dialect.name != "postgresql":
        pytest.skip("Verified localhost PostgreSQL required")
    with c.env.db.begin() as db:
        first = admit(db, c)
    results = race_runtime(
        c.env,
        f"/api/v1/runtime/commands/{first.command_id}/claim",
        [auth(c.credential, c.session["secret"])] * 2,
        [{"command_sha256": first.command_sha256}] * 2,
    )
    assert sorted(r.status_code for r in results) == [200, 409]


def race_admit(c, commands):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, Condition

    from lotuspond_runtime.errors import FleetError
    from sqlalchemy import event, select

    from lotuspond import models

    engine = c.env.db.kw["bind"]
    if engine.dialect.name != "postgresql":
        pytest.skip("Verified localhost PostgreSQL required")
    gate, reached = Barrier(2), Condition()
    connections = set()

    def observed(connection, cursor, statement, parameters, context, many):
        if "workspaces" in statement and "FOR UPDATE" in statement:
            with reached:
                connections.add(id(connection.connection.driver_connection))
                reached.notify_all()

    def run(command):
        gate.wait(timeout=5)
        try:
            with c.env.db.begin() as db:
                return admit(db, c, command)
        except FleetError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as workers:
        with c.env.db.begin() as holder:
            holder.scalar(
                select(models.Workspace)
                .where(models.Workspace.id == c.env.workspace)
                .with_for_update()
            )
            event.listen(engine, "before_cursor_execute", observed)
            try:
                futures = [workers.submit(run, command) for command in commands]
                with reached:
                    assert reached.wait_for(lambda: len(connections) == 2, timeout=5)
            finally:
                event.remove(engine, "before_cursor_execute", observed)
        return [f.result(timeout=15) for f in futures]


def test_equal_admissions_have_one_durable_record(command_env):
    from sqlalchemy import func, select

    from lotuspond.models import RuntimeCommandAdmission

    c = command_env
    results = race_admit(c, [c.case.command, c.case.command])
    assert results[0] == results[1]
    assert results[0].status == "admitted"
    with c.env.db() as db:
        assert db.scalar(select(func.count()).select_from(RuntimeCommandAdmission)) == 1


@pytest.mark.parametrize("change", [{"command_id": "command-2"}, {"replay_id": "replay-2"}])
def test_changed_replay_ids_conflict_across_connections(command_env, change):
    c = command_env
    results = race_admit(c, [c.case.command, c.case.command.model_copy(update=change)])
    assert sum(result == "conflict" for result in results) == 1
    assert sum(getattr(result, "status", None) == "admitted" for result in results) == 1
