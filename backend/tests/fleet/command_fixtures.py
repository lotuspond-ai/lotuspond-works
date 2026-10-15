"""Test-only exact command-to-record linkage and current external authorities."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from lotuspond_runtime.protocol import RuntimeIdentity

from tests.fleet.identity_fixtures import acquire, enrolled
from tests.fleet.test_management import CONFIG, POLICY, create, post

_path = Path(__file__).resolve().parents[3] / "runtime/python/tests/command_fixtures.py"
_spec = importlib.util.spec_from_file_location("runtime_command_fixtures", _path)
helpers = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(helpers)


@pytest.fixture
def command_env(identity_env):
    from lotuspond.fleet.commands import CommandService

    env = identity_env
    connection = create(env.client, env.prefix)
    response = post(
        env.client, f"{env.prefix}/connections/{connection['id']}/versions", CONFIG, "config"
    )
    assert response.status_code == 201, response.text
    response = post(
        env.client,
        f"{env.prefix}/runtime-pools/{env.pool}/policies",
        {**POLICY, "connection_ids": [connection["id"]]},
        "commands-policy",
    )
    assert response.status_code == 201, response.text
    credential = enrolled(env)
    session = acquire(env, credential)
    assert session.status_code == 201, session.text
    case = helpers.command_case(
        identity=RuntimeIdentity.model_validate(session.json()["identity"]),
        now=env.clock.now,
        connection_id=connection["id"],
    )
    pinned = helpers.records(case)

    class Records:
        def load(self, db, command):
            # Real future source must perform this linkage from persisted deployment records.
            if command.intent_sha256 != case.command.intent_sha256:
                from lotuspond_runtime.errors import FleetError

                raise FleetError("forbidden")
            return pinned

    class Dispatch:
        allowed = True

        def callback(self):
            pass

        def authorize(self, identity, digest, records):
            self.callback()
            if not self.allowed or records != pinned or identity != case.identity:
                from lotuspond_runtime.errors import FleetError

                raise FleetError("forbidden")

    class Trust:
        revision = 1
        keys = case.trust
        available = True

        def callback(self):
            pass

        def current(self, installation_id):
            self.callback()
            if not self.available:
                from lotuspond_runtime.errors import FleetError

                raise FleetError("trust_unavailable")
            from lotuspond_runtime.protocol import CommandTrustSnapshot

            return CommandTrustSnapshot(self.revision, self.keys, env.clock.now + 60000)

    dispatch, trust, source = Dispatch(), Trust(), Records()
    service = CommandService(
        env.dependencies.recovery, dispatch, source, trust, dependencies=env.dependencies
    )
    env.app.state.command_service = service
    return SimpleNamespace(
        env=env,
        case=case,
        pinned=pinned,
        source=source,
        dispatch=dispatch,
        trust=trust,
        service=service,
        credential=credential,
        session=session.json(),
        helpers=helpers,
    )


def principal(db, c):
    from lotuspond.fleet.authentication import authenticate_runtime

    return authenticate_runtime(
        db,
        c.credential["token"],
        session_secret=c.session["secret"],
        require_session=True,
        dependencies=c.env.dependencies,
    )


def admit(db, c, command=None):
    delivery = c.helpers.delivery(c.case, command)
    return c.service.admit(db, c.case.identity, delivery.command_bytes, delivery.signature_bytes)
