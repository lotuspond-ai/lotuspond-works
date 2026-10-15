"""Exact installed dependency metadata, package origin and pure import boundary."""

from conftest import artifact_run


def test_installed_metadata(installed_runtime, tmp_path):
    venv, env = installed_runtime
    artifact_run(
        [
            str(venv / "bin/python"),
            "-I",
            "-c",
            """
import importlib.metadata as m, importlib.util, sys
from pathlib import Path
import lotuspond_runtime, lotuspond_runtime.protocol, lotuspond_sdk
assert m.version('lotuspond-runtime') == '0.1.0'
assert m.version('lotuspond-sdk') == '0.3.0'
assert set(m.requires('lotuspond-runtime')) == {'lotuspond-sdk==0.3.0', 'pydantic<3,>=2.12', 'rfc8785==0.1.4'}
assert set(m.requires('lotuspond-sdk')) == {'cryptography<51,>=50', 'pydantic<3,>=2.12', 'rfc8785==0.1.4'}
for module in (lotuspond_runtime, lotuspond_sdk):
 assert Path(module.__file__).is_relative_to(Path(sys.prefix))
 assert Path(module.__file__).with_name('py.typed').is_file()
for name in ('fastapi', 'sqlalchemy', 'psycopg', 'redis', 'httpx', 'lotuspond'):
 assert importlib.util.find_spec(name) is None
 assert name not in sys.modules
assert 'lotuspond_runtime.client' not in sys.modules
from lotuspond_runtime.protocol import decode_fleet_json, connection_digest
assert connection_digest(decode_fleet_json(b'{"connector":{"name":"lotuspond-fixture","version":"1.0.0","artifact_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},"operations":["inspect"],"destinations":[],"secrets":[],"data_handling":{},"format_version":1}')) == 'b986684ecd1f4fca794d3ad8dfc808ebabcc311f256992de86dabc6e0cb768e8'
""",
        ],
        tmp_path,
        env,
    )


def test_installed_strict_boundaries(installed_runtime, tmp_path):
    venv, env = installed_runtime
    artifact_run(
        [
            str(venv / "bin/python"),
            "-I",
            "-c",
            """
import json
from pydantic import SecretStr, TypeAdapter
from lotuspond_runtime.protocol import (
    FleetError, Scope, PreparedCredential, CredentialState,
    decode_fleet_json, encode_prepared_credential,
)
uuid = '01234567-89ab-cdef-0123-456789abcdef'
scope = dict(workspace_id=uuid, project_id=uuid, environment_id=uuid)
def rejected(call):
    try:
        call()
    except FleetError as error:
        assert error.code == 'invalid_request'
        assert str(error) == 'invalid_request'
    else:
        raise AssertionError('installed fleet boundary accepted invalid input')
raw = json.dumps(scope).encode()
for content in (raw, b'{"workspace_id":"' + uuid.encode() + b'",' + raw[1:],
                raw + b' ' * 65536):
    rejected(lambda: TypeAdapter(Scope).validate_json(content))
assert Scope.model_validate_json(raw).workspace_id == uuid
assert TypeAdapter(Scope).validate_python(decode_fleet_json(raw)).workspace_id == uuid
for extra in ('ignore', 'allow'):
    for validate in (Scope.model_validate, TypeAdapter(Scope).validate_python):
        rejected(lambda: validate(scope | {'secret_extra': 'synthetic'}, extra=extra))
    dto = dict(token='synthetic', credential_version=1, activation_deadline_ms=60,
               server_time_ms=0, secret_extra='synthetic')
    for validate in (PreparedCredential.model_validate,
                     TypeAdapter(PreparedCredential).validate_python):
        rejected(lambda: validate(dto, extra=extra))
for inner in (42, None, True, 1.5, ['unexpected'], {'unexpected': 'synthetic'}):
    rejected(lambda: PreparedCredential(token=SecretStr(inner), credential_version=1,
                                        activation_deadline_ms=60, server_time_ms=0))
    model = PreparedCredential(token=SecretStr('synthetic'), credential_version=1,
                               activation_deadline_ms=60, server_time_ms=0)
    object.__setattr__(model.token, '_secret_value', inner)
    rejected(lambda: encode_prepared_credential(model))
    state = CredentialState(revision=0, instance_id=uuid, active_token='synthetic',
                            pending_token=SecretStr('pending-synthetic'), pending_version=1)
    object.__setattr__(state.pending_token, '_secret_value', inner)
    rejected(lambda: CredentialState.model_validate(state))
""",
        ],
        tmp_path,
        env,
    )
