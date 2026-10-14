import importlib
import json

import pytest
from pydantic import SecretStr

from lotuspond_runtime.errors import FleetError
from lotuspond_runtime.protocol import RuntimeObservation

UUID = "01234567-89ab-cdef-0123-456789abcdef"
TOKEN = SecretStr("lpr_" + "A" * 43)
SESSION = SecretStr("lps_" + "A" * 43)
IDENTITY = dict(
    scope=dict(workspace_id=UUID, project_id=UUID, environment_id=UUID),
    installation_id="test-install",
    recovery_epoch=1,
    pool_id=UUID,
    instance_id=UUID,
    credential_version=1,
    generation=1,
)
STATE = dict(
    identity=IDENTITY,
    credential_expires_at_ms=900000,
    session_expires_at_ms=60000,
    server_time_ms=0,
)


def client_module():
    assert importlib.util.find_spec("lotuspond_runtime.client"), "missing typed client"
    return importlib.import_module("lotuspond_runtime.client")


class RecordingTransport:
    def __init__(self, response):
        self.response, self.calls = response, []

    def request(self, *args):
        self.calls.append(args)
        return json.dumps(self.response).encode()


def test_typed_routes_and_encodings():
    module = client_module()
    for name, args, response, route, body in (
        (
            "enroll",
            (SecretStr("lpe_" + "A" * 43), RuntimeObservation(platform="linux", connectors=())),
            dict(token=TOKEN.get_secret_value(), state=STATE),
            "/enroll",
            b'{"platform":"linux","connectors":[]}',
        ),
        ("self_state", (TOKEN,), STATE, "/self", None),
        (
            "acquire",
            (TOKEN,),
            dict(
                secret=SESSION.get_secret_value(),
                identity=IDENTITY,
                expires_at_ms=60000,
                server_time_ms=0,
            ),
            "/session",
            b"{}",
        ),
        ("heartbeat", (TOKEN, SESSION), STATE, "/heartbeat", b"{}"),
        (
            "prepare",
            (TOKEN, SESSION),
            dict(
                token=TOKEN.get_secret_value(),
                credential_version=2,
                activation_deadline_ms=60000,
                server_time_ms=0,
            ),
            "/credentials/prepare",
            b"{}",
        ),
        ("activate", (TOKEN, None), STATE, "/credentials/activate", b"{}"),
        ("cancel", (TOKEN, SESSION), STATE, "/credentials/cancel", b"{}"),
    ):
        transport = RecordingTransport(response)
        result = getattr(module.RuntimeClient(transport), name)(*args)
        assert result is not None
        call = transport.calls[0]
        assert call[1:3] == (route, body)
        assert call[0] == ("GET" if route == "/self" else "POST")
    assert TOKEN.get_secret_value() not in repr(result)


@pytest.mark.parametrize("bad", [dict(STATE, extra="bad"), dict(STATE, server_time_ms=True)])
def test_response_revalidation_and_secret_shape(bad):
    module = client_module()
    with pytest.raises(FleetError):
        module.RuntimeClient(RecordingTransport(bad)).self_state(TOKEN)
    with pytest.raises(FleetError):
        module.RuntimeClient(RecordingTransport(dict(token="wrong", state=STATE))).enroll(
            SecretStr("lpe_" + "A" * 43), RuntimeObservation(platform="linux", connectors=())
        )


@pytest.mark.parametrize("method", ["heartbeat", "prepare", "cancel"])
def test_required_session_is_validated_before_transport(method):
    transport = RecordingTransport(STATE)
    with pytest.raises(FleetError):
        getattr(client_module().RuntimeClient(transport), method)(TOKEN, None)
    assert not transport.calls


def test_constructed_observation_and_duplicate_response_refused():
    module = client_module()
    transport = RecordingTransport(STATE)
    bad = RuntimeObservation.model_construct(platform="windows", connectors=())
    with pytest.raises(FleetError):
        module.RuntimeClient(transport).enroll(SecretStr("lpe_" + "A" * 43), bad)
    assert not transport.calls
    transport.request = lambda *args: b'{"identity":{},"identity":{}}'
    with pytest.raises(FleetError):
        module.RuntimeClient(transport).self_state(TOKEN)
