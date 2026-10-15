"""Verify imports from both installed distribution origins outside checkout."""

import json

from conftest import artifact_run


def test_wheel_and_sdist_are_standalone(installed_sdk, tmp_path, golden_bytes, invoke):
    venv, env = installed_sdk
    probe = artifact_run(
        [
            str(venv / "bin/python"),
            "-I",
            "-c",
            """
import importlib.metadata, importlib.util, json, sys
from pathlib import Path
import lotuspond_sdk
assert Path(lotuspond_sdk.__file__).is_relative_to(Path(sys.prefix))
assert Path(lotuspond_sdk.__file__).with_name('py.typed').is_file()
for forbidden in ('fastapi', 'sqlalchemy', 'psycopg', 'redis', 'httpx', 'lotuspond', 'jsonschema'):
    assert importlib.util.find_spec(forbidden) is None, forbidden
assert not any(name.split('.')[0] in ('fastapi', 'sqlalchemy', 'psycopg', 'redis', 'httpx', 'lotuspond', 'jsonschema') for name in sys.modules)
assert importlib.metadata.version('lotuspond-sdk') == '0.3.0'
print(json.dumps({'module': lotuspond_sdk.__file__}))
""",
        ],
        tmp_path,
        env,
    )
    assert str(venv) in json.loads(probe.stdout)["module"]
    path = tmp_path / "workflow.json"
    path.write_bytes(golden_bytes)
    for module in (False, True):
        result = invoke("validate", path, module=module)
        assert result.returncode == 0
        assert result.stderr == ""
        assert json.loads(result.stdout) == {
            "ok": True,
            "schema_version": "lotuspond.workflow-package.v1",
            "sha256": "305efc96011ba5fc2f527cefbe416890b0265269e18e3402354ffbfed40251d3",
        }


def test_installed_v2_complete_journey(installed_sdk, tmp_path, golden_bytes, invoke):
    import hashlib
    import os
    from pathlib import Path

    from cryptography.hazmat.primitives import serialization

    examples = Path(__file__).resolve().parents[3] / "contracts/examples/executable-v2"
    paths = {}
    for name in ("minimal", "catalog", "subworkflow", "capability-profile", "requirements"):
        paths[name] = tmp_path / f"{name}.json"
        paths[name].write_bytes((examples / f"{name}.json").read_bytes())
    v1 = tmp_path / "v1.json"
    v1.write_bytes(golden_bytes)

    def run(*args, status=0):
        result = invoke(*args)
        assert result.returncode == status, (args[0], result.stdout, result.stderr)
        assert result.stderr == "" and result.stdout.isascii()
        return json.loads(result.stdout)

    package = paths["minimal"]
    validated = run("v2", "validate", package)
    module_result = invoke("v2", "validate", package, module=True)
    assert module_result.returncode == 0 and json.loads(module_result.stdout) == validated
    assert validated["result"]["compilation_status"] == "not_checked"
    assert validated["result"]["authority_granted"] is False
    run("validate", package, status=2)
    run("v2", "validate", v1, status=2)
    assert (
        run("validate", v1)["sha256"]
        == "305efc96011ba5fc2f527cefbe416890b0265269e18e3402354ffbfed40251d3"
    )
    digest = run("v2", "digest", package)["result"]["sha256"]
    canonical = tmp_path / "canonical"
    run("v2", "canonicalize", package, "--output", canonical)
    assert hashlib.sha256(canonical.read_bytes()).hexdigest() == digest
    run("v2", "canonicalize", package, "--output", canonical, status=2)
    plan = tmp_path / "plan"
    empty_catalog = tmp_path / "empty-catalog"
    empty_catalog.write_text('{"documents":[]}')
    run("v2", "compile", package, "--catalog", empty_catalog, "--output", plan)
    assert (
        plan.read_bytes()
        == (Path(__file__).parent / "fixtures/executable-v2/plan_minimal.jcs").read_bytes()
    )
    subplan = tmp_path / "subplan"
    run(
        "v2",
        "compile",
        paths["subworkflow"],
        "--catalog",
        empty_catalog,
        "--dependency",
        package,
        "--output",
        subplan,
    )
    assert (
        subplan.read_bytes()
        == (Path(__file__).parent / "fixtures/executable-v2/plan_subworkflow.jcs").read_bytes()
    )
    changed = tmp_path / "changed"
    data = json.loads(package.read_bytes())
    data["name"] = "PRIVATE_SENTINEL"
    changed.write_text(json.dumps(data))
    difference = run("v2", "diff", package, changed)
    assert difference["result"]["changes"]
    assert "PRIVATE_SENTINEL" not in json.dumps(difference)
    assert "PRIVATE_SENTINEL" in json.dumps(run("v2", "diff", package, changed, "--include-values"))
    private, public, envelope, trust = [
        tmp_path / name for name in ("private", "public", "signature", "trust")
    ]
    keyid = run("keygen", "--private", private, "--public", public)["result"]["key_id"]
    pubkey = serialization.load_pem_public_key(public.read_bytes()).public_bytes_raw().hex()
    trust.write_text(
        json.dumps(
            {
                "format_version": 1,
                "keys": [{"key_id": keyid, "public_key": pubkey, "state": "allowed"}],
            }
        )
    )
    run("v2", "sign", package, "--key", private, "--output", envelope)
    assert run("v2", "verify", package, "--signature", envelope, "--trust", trust)["result"][
        "valid"
    ]
    run("v2", "verify", changed, "--signature", envelope, "--trust", trust, status=3)
    run(
        "v2",
        "compatibility",
        package,
        "--profile",
        paths["capability-profile"],
        "--requirements",
        paths["requirements"],
        "--catalog",
        empty_catalog,
    )
    profile = json.loads(paths["capability-profile"].read_bytes())
    for field in profile["supported_limits"]:
        profile["supported_limits"][field] = 0
    refused_profile = tmp_path / "refused-profile"
    refused_profile.write_text(json.dumps(profile))
    refusal = run(
        "v2",
        "compatibility",
        package,
        "--profile",
        refused_profile,
        "--requirements",
        paths["requirements"],
        "--catalog",
        empty_catalog,
        status=3,
    )
    assert refusal["result"]["compatible"] is False
    assert refusal["result"]["authority_granted"] is False
    for option, limit in (("--profile", 65536), ("--requirements", 16384)):
        oversized = tmp_path / "auxiliary-oversized"
        oversized.write_bytes(b" " * (limit + 1))
        profile_path = oversized if option == "--profile" else paths["capability-profile"]
        requirements_path = oversized if option == "--requirements" else paths["requirements"]
        failure = run(
            "v2",
            "compatibility",
            package,
            "--profile",
            profile_path,
            "--requirements",
            requirements_path,
            "--catalog",
            empty_catalog,
            status=2,
        )
        assert failure["errors"][0]["code"] == "input_too_large"
    for kind in ("package", "plan", "runtime"):
        run("v2", "schema", "--kind", kind, "--output", tmp_path / f"{kind}.schema")
    for content in (b"{", b'{"x":1,"x":2}', b" " * 1048577):
        changed.write_bytes(content)
        run("v2", "validate", changed, status=2)
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    run("v2", "validate", fifo, status=2)
    assert "PRIVATE_SENTINEL" not in json.dumps(
        run("v2", "validate", "--PRIVATE_SENTINEL", status=2)
    )
    venv, env = installed_sdk
    artifact_run(
        [
            str(venv / "bin/python"),
            "-I",
            "-c",
            """
import importlib.util, sys
from pathlib import Path
from lotuspond_sdk.executable import compile_package
import lotuspond_sdk.executable.cli, lotuspond_sdk.executable.schema
assert Path(lotuspond_sdk.executable.cli.__file__).is_relative_to(Path(sys.prefix))
for name in ('fastapi', 'sqlalchemy', 'psycopg', 'redis', 'httpx', 'lotuspond', 'jsonschema'):
    assert importlib.util.find_spec(name) is None
    assert name not in sys.modules
""",
        ],
        tmp_path,
        env,
    )
    # Real installed file publication retains an incomplete output on fsync failure.
    partial = tmp_path / "partial"
    probe = artifact_run(
        [
            str(venv / "bin/python"),
            "-I",
            "-c",
            """
import sys
from lotuspond_sdk import files
from lotuspond_sdk.cli import main
def fail(_): raise OSError('PRIVATE_SENTINEL')
files.os.fsync = fail
assert main(['v2', 'canonicalize', sys.argv[1], '--output', sys.argv[2]]) == 2
""",
            str(package),
            str(partial),
        ],
        tmp_path,
        env,
    )
    assert "PRIVATE_SENTINEL" not in probe.stdout
    assert json.loads(probe.stdout)["errors"][0]["code"] == "output_incomplete"
    assert partial.is_file() and partial.stat().st_size > 0


def test_installed_activity_literal_and_inert_import(installed_sdk, tmp_path):
    from pathlib import Path

    venv, env = installed_sdk
    fixture_dir = Path(__file__).with_name("activity") / "fixtures"
    for name in ("read.json", "read.canonical.json", "read.signature.json"):
        (tmp_path / name).write_bytes((fixture_dir / name).read_bytes())
    artifact_run([
        str(venv / "bin/python"), "-I", "-c", """
import importlib.metadata, importlib.util, json, socket, sys
from pathlib import Path
def refuse(*args, **kwargs): raise AssertionError('import attempted I/O')
socket.socket = refuse
import lotuspond_sdk.activity as activity
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
assert Path(activity.__file__).is_relative_to(Path(sys.prefix))
assert importlib.metadata.version('lotuspond-sdk') == '0.3.0'
for forbidden in ('temporalio', 'fastapi', 'sqlalchemy', 'psycopg', 'redis', 'httpx',
                  'lotuspond', 'lotuspond_runtime', 'lotuspond_orchestrator', 'jsonschema'):
    assert importlib.util.find_spec(forbidden) is None, forbidden
    assert forbidden not in sys.modules
raw = json.loads(Path('read.json').read_bytes())
literal = json.loads(Path('read.signature.json').read_bytes())
assert activity.canonical_activity_json(raw) == Path('read.canonical.json').read_bytes()
assert activity.activity_digest(raw) == '06abd378a53efb058bf9b880d97f1952d23db32a50b8ffd15d8a658fc06e2203'
key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(
    '9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60'))
assert activity.sign_activity(raw, key).model_dump() == literal
trust = {'format_version':1,'keys':[{'key_id':literal['key_id'],
         'public_key':key.public_key().public_bytes_raw().hex(),'state':'allowed'}]}
report = activity.verify_activity(raw, literal, trust)
assert report.valid and report.authority_granted is False
delivery = json.dumps({'envelope':json.dumps(raw),'signature':json.dumps(literal)}).encode()
assert activity.decode_activity_delivery(delivery)[0].model_dump() == raw
assert activity.activity_schema()['$schema'].endswith('/2020-12/schema')
print(json.dumps({'module':activity.__file__, 'version':importlib.metadata.version('lotuspond-sdk')}))
"""], tmp_path, env)
