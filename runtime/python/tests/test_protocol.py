"""Frozen fleet wire identities and untrusted-input boundaries."""

import copy
import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import SecretStr

FIXTURES = Path(__file__).parent / "fixtures"
UUID = "01234567-89ab-cdef-0123-456789abcdef"


def api():
    # A missing distribution is intentional behavioral red, not a collection error.
    return importlib.import_module("lotuspond_runtime.protocol")


def connection():
    return json.loads((FIXTURES / "connection-v1.json").read_bytes())


def pool():
    return json.loads((FIXTURES / "pool-v1.json").read_bytes())


def reject(call, code="invalid_request"):
    from lotuspond_runtime.errors import FleetError

    with pytest.raises(FleetError) as caught:
        call()
    assert caught.value.code == code
    assert str(caught.value) == code
    assert "PRIVATE_SENTINEL" not in repr(caught.value)


def test_connection_literal_canonical_fixture():
    p = api()
    literal = (FIXTURES / "connection-v1.canonical.json").read_bytes()
    assert len(literal) == 336
    assert p.canonical_connection_json(connection()) == literal
    assert (
        p.connection_digest(connection())
        == "b986684ecd1f4fca794d3ad8dfc808ebabcc311f256992de86dabc6e0cb768e8"
    )
    pool_bytes = (FIXTURES / "pool-v1.json").read_bytes()
    assert len(pool_bytes) == 258
    assert p.canonical_pool_json(pool()) == pool_bytes
    assert (
        p.pool_digest(pool()) == "ad5efd0394a447567a58c2c9df3f8febcf2ebe69d36e7ee6e57a9af36c3d65dd"
    )
    probe = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            """
import fs from 'node:fs'; import crypto from 'node:crypto';
for (const [path, domain] of [[process.argv[1], 'lotuspond-connection-config-v1'], [process.argv[2], 'lotuspond-runtime-pool-policy-v1']]) {
 const raw = fs.readFileSync(path); console.log(crypto.createHash('sha256').update(domain+'\\0').update(raw).digest('hex'));
}
""",
            str(FIXTURES / "connection-v1.canonical.json"),
            str(FIXTURES / "pool-v1.json"),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert probe.stdout.splitlines() == [p.connection_digest(connection()), p.pool_digest(pool())]


@pytest.mark.parametrize(
    "content,code",
    [
        (b'{"x":1,"x":2}', "invalid_request"),
        (b"{", "invalid_request"),
        (b"NaN", "invalid_request"),
        (b"Infinity", "invalid_request"),
        (b'"\\ud800"', "invalid_request"),
        (b'"\xff"', "invalid_request"),
        (b"0" * 65537, "input_limit"),
        (b'"' + b"a" * 8193 + b'"', "input_limit"),
        (b"[" * 17 + b"0" + b"]" * 17, "input_limit"),
        (b"[" + b"0," * 4096 + b"0]", "input_limit"),
        (b"9007199254740992", "invalid_request"),
        (b"1e309", "invalid_request"),
    ],
)
def test_decoder_rejects_unsafe_input(content, code):
    reject(lambda: api().decode_fleet_json(content), code)


def test_decoder_preserves_unicode_and_boundaries():
    p = api()
    assert p.decode_fleet_json('"é🌿"'.encode()) == "é🌿"
    assert p.decode_fleet_json(b'"' + b"a" * 8192 + b'"') == "a" * 8192
    assert p.decode_fleet_json(b"[" * 16 + b"0" + b"]" * 16)
    assert len(p.decode_fleet_json(b"[" + b"0," * 4094 + b"0]")) == 4095
    assert p.decode_fleet_json(b"0" + b" " * 65535) == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("format_version", True),
        ("format_version", 1.0),
        ("format_version", 2),
        ("operations", []),
        ("operations", ["inspect"] * 2),
        ("operations", ["a" + str(n) for n in range(33)]),
        ("operations", ["A"]),
        ("operations", ["a" * 65]),
        ("operations", ["a/inspect"]),
        ("secrets", [{"reference_id": UUID, "version": True}]),
        ("secrets", [{"reference_id": UUID, "version": 0}]),
        ("secrets", [{"reference_id": UUID, "version": 2147483648}]),
        ("secrets", [{"reference_id": UUID.upper(), "version": 1}]),
        ("unknown", "PRIVATE_SENTINEL"),
    ],
)
def test_connection_strict_fields(field, value):
    raw = connection()
    raw[field] = value
    reject(lambda: api().validate_connection_config(raw))


@pytest.mark.parametrize(
    "field,upper",
    [("max_request_bytes", 65536), ("max_response_bytes", 1048576), ("timeout_ms", 30000)],
)
@pytest.mark.parametrize("position", ["below", "min", "max", "above", "bool", "float"])
def test_data_handling_bounds(field, upper, position):
    raw = connection()
    value = {"below": 0, "min": 1, "max": upper, "above": upper + 1, "bool": True, "float": 1.0}[
        position
    ]
    raw["data_handling"][field] = value
    if position in ("min", "max"):
        assert getattr(api().validate_connection_config(raw).data_handling, field) == value
    else:
        reject(lambda: api().validate_connection_config(raw))


@pytest.mark.parametrize(
    "field,upper", [("max_instances", 1000), ("max_concurrent_invocations", 32)]
)
@pytest.mark.parametrize("value_kind", ["below", "min", "max", "above", "bool", "float"])
def test_pool_integer_bounds(field, upper, value_kind):
    raw = pool()
    value = {"below": 0, "min": 1, "max": upper, "above": upper + 1, "bool": True, "float": 1.0}[
        value_kind
    ]
    raw[field] = value
    if value_kind in ("min", "max"):
        assert getattr(api().validate_pool_policy(raw), field) == value
    else:
        reject(lambda: api().validate_pool_policy(raw))


def test_revalidates_constructed_mutated_nested_and_cyclic_models():
    p = api()
    model = p.validate_connection_config(connection())
    object.__setattr__(model.data_handling, "max_request_bytes", True)
    reject(lambda: p.connection_digest(model))
    invalid = p.ConnectionConfig.model_construct(**connection(), unknown="PRIVATE_SENTINEL")
    invalid.__dict__["unknown"] = "PRIVATE_SENTINEL"
    reject(lambda: p.validate_connection_config(invalid))
    raw = connection()
    raw["operations"] = raw
    reject(lambda: p.canonical_connection_json(raw))
    object.__setattr__(model, "connector", model)
    reject(lambda: p.canonical_connection_json(model))
    with pytest.raises(Exception):
        p.validate_connection_config(connection()).format_version = 2


def test_sets_are_unique_and_canonically_sorted():
    p = api()
    raw = connection()
    raw["operations"] = ["write", "inspect"]
    raw["destinations"] = [
        {"host": "example.com", "port": n, "path_prefix": "/é"} for n in (443, 80)
    ]
    raw["secrets"] = [{"reference_id": UUID, "version": n} for n in (10, 2)]
    result = json.loads(p.canonical_connection_json(raw))
    assert result["operations"] == ["inspect", "write"]
    assert [x["port"] for x in result["destinations"]] == [80, 443]
    assert [x["version"] for x in result["secrets"]] == [2, 10]
    assert "/é".encode() in p.canonical_connection_json(raw)
    for field in ("operations", "destinations", "secrets"):
        duplicate = copy.deepcopy(raw)
        duplicate[field].append(duplicate[field][0])
        reject(lambda: p.validate_connection_config(duplicate))
    for field in ("connectors", "connection_ids", "platforms"):
        duplicate = pool()
        duplicate[field] = [UUID, UUID] if field == "connection_ids" else duplicate[field] * 2
        reject(lambda: p.validate_pool_policy(duplicate))


@pytest.mark.parametrize(
    "raw",
    [
        {"name": "A", "version": "1.0.0", "artifact_sha256": "a" * 64},
        {"name": "fixture", "version": "01.0.0", "artifact_sha256": "a" * 64},
        {"name": "fixture", "version": "1000000.0.0", "artifact_sha256": "a" * 64},
        {"name": "fixture", "version": "1.0", "artifact_sha256": "a" * 64},
        {"name": "fixture", "version": "1.0.0", "artifact_sha256": "A" * 64},
        {"name": "fixture", "version": "1.0.0", "artifact_sha256": "a" * 63},
    ],
)
def test_connector_grammar(raw):
    reject(lambda: api().ConnectorPin(**raw))


@pytest.mark.parametrize(
    "host",
    ["EXAMPLE.com", "example.com.", "é.com", "a" * 254, "01.2.3.4", "[::1]", "::ffff:127.0.0.1"],
)
def test_destination_host_grammar(host):
    reject(lambda: api().DestinationRule(host=host, port=443, path_prefix="/"))


@pytest.mark.parametrize(
    "path", ["relative", "/a/../b", "/a//b", "/a/./b", "/a?b", "/a#b", "/a\\b", "/a%2fb", "/a\x00"]
)
def test_destination_path_normalization(path):
    reject(lambda: api().DestinationRule(host="example.com", port=443, path_prefix=path))


def identity():
    return dict(
        scope=dict(workspace_id=UUID, project_id=UUID, environment_id=UUID),
        installation_id="installation:one",
        recovery_epoch=0,
        pool_id=UUID,
        instance_id=UUID,
        credential_version=1,
        generation=0,
    )


@pytest.mark.parametrize("field", ["recovery_epoch", "generation", "credential_version"])
@pytest.mark.parametrize("value", [-1, True, 1.0, 9007199254740992])
def test_identity_integer_types(field, value):
    raw = identity()
    raw[field] = value
    reject(lambda: api().RuntimeIdentity(**raw))


def test_secret_projection_is_explicit_and_pending_is_atomic():
    p = api()
    state = p.SelfState(
        identity=p.RuntimeIdentity(**identity()),
        credential_expires_at_ms=15,
        session_expires_at_ms=None,
        server_time_ms=0,
    )
    issued = p.IssuedCredential(token=SecretStr("PRIVATE_SENTINEL"), state=state)
    prepared = p.PreparedCredential(
        token=SecretStr("PRIVATE_SENTINEL"),
        credential_version=2,
        activation_deadline_ms=60,
        server_time_ms=0,
    )
    session = p.AcquiredSession(
        secret=SecretStr("PRIVATE_SENTINEL"),
        identity=state.identity,
        expires_at_ms=60,
        server_time_ms=0,
    )
    for model, encode, key in [
        (issued, p.encode_issued_credential, "token"),
        (prepared, p.encode_prepared_credential, "token"),
        (session, p.encode_acquired_session, "secret"),
    ]:
        assert "PRIVATE_SENTINEL" not in repr(model) + model.model_dump_json()
        assert encode(model)[key] == "PRIVATE_SENTINEL"
        object.__setattr__(model, key, "PRIVATE_SENTINEL")
        reject(lambda: encode(model))
    base = dict(
        revision=0,
        instance_id=UUID,
        active_token=SecretStr("PRIVATE_SENTINEL"),
        pending_token=None,
        pending_version=None,
    )
    assert "PRIVATE_SENTINEL" not in p.CredentialState(**base).model_dump_json()
    for update in ({"pending_version": 2}, {"pending_token": SecretStr("P")}):
        reject(lambda: p.CredentialState(**(base | update)))


def test_protocol_import_is_inert():
    api()
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import builtins, importlib, io, os, socket, sys, threading
import pydantic, rfc8785, ipaddress, uuid, hashlib, json
# Preload library machinery so the sentinels measure protocol side effects.
class Warmup(pydantic.BaseModel):
 x: str
Warmup.model_json_schema()
def deny(*args, **kwargs): raise AssertionError('unexpected I/O')
builtins.open = io.open = deny
os.getenv = os.environ.__class__.__getitem__ = os.environ.__class__.get = deny
socket.socket = socket.create_connection = deny
threading.Thread.start = deny
import lotuspond_runtime, lotuspond_runtime.protocol
for name in sys.modules:
 assert not name.startswith(('lotuspond_runtime.client', 'lotuspond_runtime.control_transport', 'lotuspond_runtime.resolver', 'lotuspond_runtime.supervisor', 'lotuspond.'))
""",
        ],
        text=True,
        capture_output=True,
    )
    assert probe.returncode == 0, probe.stderr


@pytest.mark.parametrize(
    "model,field,bound",
    [
        ("connection", "destinations", 16),
        ("connection", "secrets", 16),
        ("pool", "connectors", 64),
        ("pool", "connection_ids", 64),
    ],
)
@pytest.mark.parametrize("delta", [-1, 0, 1])
def test_array_boundaries(model, field, bound, delta):
    p = api()
    raw = connection() if model == "connection" else pool()
    count = bound + delta
    values = {
        "destinations": [
            dict(host="example.com", port=n + 1, path_prefix="/") for n in range(count)
        ],
        "secrets": [dict(reference_id=UUID, version=n + 1) for n in range(count)],
        "connectors": [
            dict(name=f"fixture{n}", version="1.0.0", artifact_sha256="a" * 64)
            for n in range(count)
        ],
        "connection_ids": [f"01234567-89ab-cdef-0123-{n:012x}" for n in range(count)],
    }
    raw[field] = values[field]
    validate = p.validate_connection_config if model == "connection" else p.validate_pool_policy
    if delta <= 0:
        assert len(getattr(validate(raw), field)) == count
    else:
        reject(lambda: validate(raw))


@pytest.mark.parametrize("value", [True, 0, 1, "false"])
def test_retention_is_literal_false(value):
    raw = connection()
    raw["data_handling"]["retain_payloads"] = value
    reject(lambda: api().validate_connection_config(raw))


@pytest.mark.parametrize(
    "field", ["credential_expires_at_ms", "session_expires_at_ms", "server_time_ms"]
)
@pytest.mark.parametrize("value", [-1, True, 1.0, 9007199254740992])
def test_timestamp_is_exact_safe_integer(field, value):
    raw = dict(
        identity=identity(),
        credential_expires_at_ms=0,
        session_expires_at_ms=None,
        server_time_ms=0,
    )
    raw[field] = value
    reject(lambda: api().SelfState(**raw))


def test_pool_platform_and_observation_bounds():
    p = api()
    for platforms in ([], ["windows"], ["linux", "linux"], "linux"):
        reject(lambda: p.validate_pool_policy(pool() | {"platforms": platforms}))
    connectors = [
        dict(name=f"fixture{n}", version="1.0.0", artifact_sha256="a" * 64) for n in range(64)
    ]
    assert len(p.RuntimeObservation(platform="linux", connectors=connectors).connectors) == 64
    reject(lambda: p.RuntimeObservation(platform="linux", connectors=connectors + [connectors[0]]))
    reject(lambda: p.RuntimeObservation(platform="linux", connectors=connectors[:1] * 2))


def test_destination_byte_and_port_bounds():
    p = api()
    for port in (1, 65535):
        assert p.DestinationRule(host="::1", port=port, path_prefix="/").port == port
    for port in (0, 65536, True, 443.0):
        reject(lambda: p.DestinationRule(host="example.com", port=port, path_prefix="/"))
    path = "/" + "é" * 511 + "a"
    assert len(path.encode()) == 1024
    assert p.DestinationRule(host="example.com", port=443, path_prefix=path).path_prefix == path
    reject(lambda: p.DestinationRule(host="example.com", port=443, path_prefix=path + "a"))


def test_secret_wire_strings_are_redacted_on_validation():
    p = api()
    raw = dict(
        revision=0,
        instance_id=UUID,
        active_token="PRIVATE_SENTINEL",
        pending_token=None,
        pending_version=None,
    )
    model = p.CredentialState.model_validate(raw)
    assert isinstance(model.active_token, SecretStr)
    assert "PRIVATE_SENTINEL" not in repr(model) + model.model_dump_json()
    raw["pending_token"] = "P"
    raw["pending_version"] = 2
    assert p.CredentialState.model_validate(raw).pending_token.get_secret_value() == "P"


@pytest.mark.parametrize(
    "raw",
    [b"{", b'{"workspace_id":"PRIVATE_SENTINEL","workspace_id":"PRIVATE_SENTINEL"}', b'"\\ud800"'],
)
def test_model_json_validation_uses_bounded_decoder(raw):
    reject(lambda: api().Scope.model_validate_json(raw))


def test_mapped_ipv6_has_one_literal_form_across_interpreters():
    p = api()
    assert (
        p.DestinationRule(host="::ffff:7f00:1", port=443, path_prefix="/").host == "::ffff:7f00:1"
    )
    reject(lambda: p.DestinationRule(host="::ffff:127.0.0.1", port=443, path_prefix="/"))


def test_ip_literal_has_no_interface_zone():
    reject(lambda: api().DestinationRule(host="fe80::1%eth0", port=443, path_prefix="/"))


@pytest.mark.parametrize("size", [63, 64, 65])
def test_identifier_length_boundary(size):
    raw = connection()
    raw["connector"]["name"] = "a" * size
    if size <= 64:
        assert api().validate_connection_config(raw).connector.name == "a" * size
    else:
        reject(lambda: api().validate_connection_config(raw))


@pytest.mark.parametrize("value", [999998, 999999, 1000000])
def test_version_component_boundary(value):
    raw = connection()
    raw["connector"]["version"] = f"{value}.{value}.{value}"
    if value <= 999999:
        assert (
            api().validate_connection_config(raw).connector.version == raw["connector"]["version"]
        )
    else:
        reject(lambda: api().validate_connection_config(raw))


@pytest.mark.parametrize("size", [127, 128, 129])
def test_installation_id_boundary(size):
    raw = identity() | {"installation_id": "a" * size}
    if size <= 128:
        assert api().RuntimeIdentity(**raw).installation_id == "a" * size
    else:
        reject(lambda: api().RuntimeIdentity(**raw))


@pytest.mark.parametrize("value", [0, 1, 9007199254740990, 9007199254740991])
def test_safe_identity_integer_boundaries(value):
    assert (
        api()
        .RuntimeIdentity(
            **(identity() | dict(recovery_epoch=value, generation=value, credential_version=value))
        )
        .generation
        == value
    )


@pytest.mark.parametrize("size", [252, 253, 254])
def test_dns_host_byte_boundary(size):
    host = ".".join(["a" * 63] * 3 + ["a" * (size - 192)])
    assert len(host) == size
    if size <= 253:
        assert api().DestinationRule(host=host, port=443, path_prefix="/").host == host
    else:
        reject(lambda: api().DestinationRule(host=host, port=443, path_prefix="/"))


@pytest.mark.parametrize("size", [31, 32, 33])
def test_operation_array_boundary(size):
    raw = connection() | {"operations": [f"op{n}" for n in range(size)]}
    if size <= 32:
        assert len(api().validate_connection_config(raw).operations) == size
    else:
        reject(lambda: api().validate_connection_config(raw))


@pytest.mark.parametrize("size", [63, 64, 65])
def test_digest_length_boundary(size):
    raw = connection()
    raw["connector"]["artifact_sha256"] = "a" * size
    if size == 64:
        assert api().validate_connection_config(raw).connector.artifact_sha256 == "a" * size
    else:
        reject(lambda: api().validate_connection_config(raw))


@pytest.mark.parametrize(
    "field,value",
    [("reference_id", UUID), ("version", 1), ("version", 2147483646), ("version", 2147483647)],
)
def test_secret_pin_valid_boundaries(field, value):
    assert api().SecretPin(**(dict(reference_id=UUID, version=1) | {field: value}))


def test_empty_allowlists_grant_no_implicit_capability():
    assert (
        api().validate_pool_policy(pool() | {"connectors": [], "connection_ids": []}).connectors
        == ()
    )
    assert api().RuntimeObservation(platform="linux", connectors=[]).connectors == ()


@pytest.mark.parametrize(
    "content",
    [b"[" * 15 + b"0" + b"]" * 15, b'"' + b"a" * 8191 + b'"', b"[" + b"0," * 4093 + b"0]"],
)
def test_decoder_limits_minus_one(content):
    assert api().decode_fleet_json(content) is not None


def test_unknown_nested_fields_and_lossy_tuple_coercions_fail():
    raw = connection()
    raw["data_handling"]["PRIVATE_SENTINEL"] = "PRIVATE_SENTINEL"
    reject(lambda: api().validate_connection_config(raw))
    for array in ("inspect", {"inspect"}, {"inspect": None}):
        reject(lambda: api().validate_connection_config(connection() | {"operations": array}))


@pytest.mark.parametrize("payload_kind", ["valid", "duplicate", "over_bytes"])
def test_native_adapter_json_is_denied_before_fleet_validation(payload_kind):
    from pydantic import TypeAdapter

    p = api()
    raw = json.dumps(identity()["scope"]).encode()
    if payload_kind == "duplicate":
        raw = b'{"workspace_id":"' + UUID.encode() + b'",' + raw[1:]
    elif payload_kind == "over_bytes":
        raw += b" " * (65537 - len(raw))
    reject(lambda: TypeAdapter(p.Scope).validate_json(raw))
    if payload_kind == "valid":
        assert p.Scope.model_validate_json(raw).workspace_id == UUID
        assert TypeAdapter(p.Scope).validate_python(p.decode_fleet_json(raw)).workspace_id == UUID
    else:
        code = "input_limit" if payload_kind == "over_bytes" else "invalid_request"
        reject(lambda: p.Scope.model_validate_json(raw), code)


@pytest.mark.parametrize("extra", ["ignore", "allow"])
@pytest.mark.parametrize("entry_point", ["class", "adapter"])
@pytest.mark.parametrize("target", ["scope", "nested_scope", "prepared", "private_state"])
def test_per_call_extra_override_cannot_relax_fleet_fields(extra, entry_point, target):
    from pydantic import TypeAdapter

    p = api()
    if target == "scope":
        model = p.Scope
        raw = identity()["scope"] | {"secret_extra": "PRIVATE_SENTINEL"}
    elif target == "nested_scope":
        model = p.RuntimeIdentity
        raw = identity()
        raw["scope"]["secret_extra"] = "PRIVATE_SENTINEL"
    elif target == "prepared":
        model = p.PreparedCredential
        raw = dict(
            token="synthetic",
            credential_version=1,
            activation_deadline_ms=60,
            server_time_ms=0,
            secret_extra="PRIVATE_SENTINEL",
        )
    else:
        model = p.CredentialState
        raw = dict(
            revision=0,
            instance_id=UUID,
            active_token="synthetic",
            pending_token=None,
            pending_version=None,
            secret_extra="PRIVATE_SENTINEL",
        )
    validate = (
        model.model_validate if entry_point == "class" else TypeAdapter(model).validate_python
    )
    reject(lambda: validate(raw, extra=extra))


@pytest.mark.parametrize(
    "model_name,raw",
    [
        ("ConnectionConfig", connection()),
        ("PoolPolicy", pool()),
        (
            "IssuedCredential",
            dict(
                token="synthetic",
                state=dict(
                    identity=identity(),
                    credential_expires_at_ms=0,
                    session_expires_at_ms=None,
                    server_time_ms=0,
                ),
            ),
        ),
        (
            "AcquiredSession",
            dict(secret="synthetic", identity=identity(), expires_at_ms=60, server_time_ms=0),
        ),
    ],
)
def test_nested_native_json_mode_is_denied(model_name, raw):
    from pydantic import TypeAdapter

    p = api()
    reject(lambda: TypeAdapter(getattr(p, model_name)).validate_json(json.dumps(raw).encode()))


class DerivedString(str):
    pass


BAD_SECRET_VALUES = [
    42,
    None,
    True,
    1.5,
    ["unexpected"],
    {"unexpected": "synthetic"},
    b"synthetic",
    DerivedString("synthetic"),
]


def secret_dto_case(p, dto_name, field):
    if dto_name == "IssuedCredential":
        raw = dict(
            token=SecretStr("synthetic"),
            state=dict(
                identity=identity(),
                credential_expires_at_ms=0,
                session_expires_at_ms=None,
                server_time_ms=0,
            ),
        )
        encode = p.encode_issued_credential
    elif dto_name == "PreparedCredential":
        raw = dict(
            token=SecretStr("synthetic"),
            credential_version=1,
            activation_deadline_ms=60,
            server_time_ms=0,
        )
        encode = p.encode_prepared_credential
    elif dto_name == "AcquiredSession":
        raw = dict(
            secret=SecretStr("synthetic"), identity=identity(), expires_at_ms=60, server_time_ms=0
        )
        encode = p.encode_acquired_session
    else:
        raw = dict(
            revision=0,
            instance_id=UUID,
            active_token=SecretStr("synthetic"),
            pending_token=None,
            pending_version=None,
        )
        if field == "pending_token":
            raw.update(pending_token=SecretStr("pending-synthetic"), pending_version=1)
        encode = p.CredentialState.model_validate
    return getattr(p, dto_name), raw, encode


@pytest.mark.parametrize(
    "dto_name,field",
    [
        ("IssuedCredential", "token"),
        ("PreparedCredential", "token"),
        ("AcquiredSession", "secret"),
        ("CredentialState", "active_token"),
        ("CredentialState", "pending_token"),
    ],
)
@pytest.mark.parametrize(
    "malformed",
    BAD_SECRET_VALUES,
    ids=["integer", "none", "bool", "float", "list", "dict", "bytes", "str-subclass"],
)
@pytest.mark.parametrize("origin", ["constructor", "constructed_model", "mutated_wrapper"])
def test_malformed_secret_wrappers_fail_every_secret_boundary(dto_name, field, malformed, origin):
    p = api()
    model, raw, encode = secret_dto_case(p, dto_name, field)
    if origin == "constructor":
        raw[field] = SecretStr(malformed)
        reject(lambda: model(**raw))
    elif origin == "constructed_model":
        raw[field] = SecretStr(malformed)
        reject(lambda: encode(model.model_construct(**raw)))
    else:
        instance = model(**raw)
        object.__setattr__(getattr(instance, field), "_secret_value", malformed)
        reject(lambda: encode(instance))


@pytest.mark.parametrize("inner,code", [("\ud800", "invalid_request"), ("a" * 8193, "input_limit")])
def test_existing_secret_wrappers_retain_unicode_and_string_budgets(inner, code):
    p = api()
    model = p.PreparedCredential(
        token=SecretStr("synthetic"),
        credential_version=1,
        activation_deadline_ms=60,
        server_time_ms=0,
    )
    object.__setattr__(model.token, "_secret_value", inner)
    reject(lambda: p.encode_prepared_credential(model), code)


def test_missing_secret_wrapper_inner_value_has_safe_error():
    p = api()
    model = p.PreparedCredential(
        token=SecretStr("synthetic"),
        credential_version=1,
        activation_deadline_ms=60,
        server_time_ms=0,
    )
    object.__delattr__(model.token, "_secret_value")
    reject(lambda: p.encode_prepared_credential(model))
