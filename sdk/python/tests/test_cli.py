"""Real installed CLI journeys and value-free failures for both wheel origins."""

import copy
import hashlib
import json
import os
import stat
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

GOLDEN = "305efc96011ba5fc2f527cefbe416890b0265269e18e3402354ffbfed40251d3"
MARKER = "private-sentinel-do-not-echo"
EXAMPLES = Path(__file__).resolve().parents[3] / "contracts/examples"


def report(result, status=0):
    assert result.returncode == status, result.stdout + result.stderr
    assert result.stderr == ""
    assert len(result.stdout.encode("ascii")) <= 8 * 1024 * 1024
    parsed = json.loads(result.stdout)
    assert parsed["ok"] is (status == 0)
    return parsed


def failure(result, code=None):
    parsed = report(result, 2)
    assert MARKER not in result.stdout + result.stderr
    assert 1 <= len(parsed["errors"]) <= 20
    if code:
        assert parsed["errors"][0]["code"] == code
    return parsed


@pytest.fixture
def package_path(tmp_path, golden_bytes):
    path = tmp_path / "workflow.json"
    path.write_bytes(golden_bytes)
    return path


@pytest.fixture
def signed(invoke, package_path, tmp_path):
    private, public = tmp_path / "private.pem", tmp_path / "public.pem"
    key_id = report(invoke("keygen", "--private", private, "--public", public))["result"]["key_id"]
    public_key = serialization.load_pem_public_key(public.read_bytes()).public_bytes_raw()
    assert key_id == hashlib.sha256(public_key).hexdigest()
    trust = tmp_path / "trust.json"
    trust.write_text(
        json.dumps(
            {
                "format_version": 1,
                "keys": [
                    {
                        "key_id": key_id,
                        "public_key": public_key.hex(),
                        "state": "allowed",
                    }
                ],
            }
        )
    )
    signature = tmp_path / "signature.json"
    report(invoke("sign", package_path, "--key", private, "--output", signature))
    return private, public, signature, trust


def test_validation_digest_canonicalize_and_diff(invoke, package_path, golden_bytes, tmp_path):
    for module in (False, True):
        assert report(invoke("validate", package_path, module=module)) == {
            "ok": True,
            "schema_version": "lotuspond.workflow-package.v1",
            "sha256": GOLDEN,
        }
        assert report(invoke("digest", package_path, module=module))["result"]["sha256"] == GOLDEN
    canonical = tmp_path / "canonical.json"
    assert (
        report(invoke("canonicalize", package_path, "--output", canonical))["result"]["sha256"]
        == GOLDEN
    )
    assert canonical.read_bytes() == golden_bytes
    failure(invoke("canonicalize", package_path, "--output", canonical), "output_exists")
    assert canonical.read_bytes() == golden_bytes
    raw = json.loads(golden_bytes)
    raw["nodes"][2]["instructions"] = MARKER
    changed = tmp_path / "changed.json"
    changed.write_text(json.dumps(raw))
    default = invoke("diff", package_path, changed)
    changes = report(default)["result"]["changes"]
    assert changes and any(change["category"] == "instructions" for change in changes)
    assert MARKER not in default.stdout
    assert (
        MARKER
        in report(invoke("diff", package_path, changed, "--include-values"))["result"]["changes"][
            0
        ]["values"]["after"]
    )
    assert (
        report(invoke("diff", package_path, changed))["result"]
        == json.loads(default.stdout)["result"]
    )


def test_trust_lifecycle_and_exclusive_outputs(invoke, package_path, signed, tmp_path):
    private, public, signature, trust = signed
    assert stat.S_IMODE(private.stat().st_mode) == 0o600
    private_body = private.read_text()
    assert private_body.startswith("-----BEGIN PRIVATE KEY-----")
    result = invoke("verify", package_path, "--signature", signature, "--trust", trust)
    verified = report(result)["result"]
    assert verified["valid"] is True and verified["code"] == "verified"
    assert verified["authority_granted"] is False
    assert private_body not in result.stdout
    for command in (
        ("keygen", "--private", private, "--public", public),
        ("sign", package_path, "--key", private, "--output", signature),
    ):
        snapshots = {path: path.read_bytes() for path in (private, public, signature)}
        failure(invoke(*command), "output_exists")
        assert all(path.read_bytes() == data for path, data in snapshots.items())
    empty = tmp_path / "empty-trust.json"
    empty.write_text('{"format_version":1,"keys":[]}')
    refused = report(invoke("verify", package_path, "--signature", signature, "--trust", empty), 3)
    assert refused["result"]["code"] == "untrusted_key"
    envelope = json.loads(signature.read_bytes())
    original_signature = signature.read_bytes()
    envelope["signature"] = ("0" if envelope["signature"][0] != "0" else "1") + envelope[
        "signature"
    ][1:]
    signature.write_text(json.dumps(envelope))
    refused = report(invoke("verify", package_path, "--signature", signature, "--trust", trust), 3)
    assert refused["result"]["code"] == "invalid_signature"
    signature.write_bytes(original_signature)
    ambiguous = json.loads(trust.read_bytes())
    duplicate = copy.deepcopy(ambiguous["keys"][0])
    duplicate["state"] = "revoked"
    ambiguous["keys"].append(duplicate)
    original_trust = trust.read_bytes()
    trust.write_text(json.dumps(ambiguous))
    failure(
        invoke("verify", package_path, "--signature", signature, "--trust", trust),
        "invalid_trust_set",
    )
    trust.write_bytes(original_trust)
    raw = json.loads(package_path.read_bytes())
    raw["nodes"][2]["instructions"] = MARKER
    package_path.write_text(json.dumps(raw))
    refused = report(invoke("verify", package_path, "--signature", signature, "--trust", trust), 3)
    assert refused["result"]["code"] == "digest_mismatch"
    revoked = json.loads(trust.read_bytes())
    revoked["keys"][0]["state"] = "revoked"
    trust.write_text(json.dumps(revoked))
    refused = report(invoke("verify", package_path, "--signature", signature, "--trust", trust), 3)
    assert refused["result"]["code"] == "revoked_key"
    assert MARKER not in json.dumps(refused)


def test_full_and_partial_compatibility(invoke, package_path, tmp_path):
    profile = tmp_path / "profile.json"
    requirements = tmp_path / "requirements.json"
    complete = json.loads((EXAMPLES / "synthetic-capabilities.v1.json").read_bytes())
    requirements.write_bytes((EXAMPLES / "synthetic-requirements.v1.json").read_bytes())
    profile.write_text(json.dumps(complete))
    checked = report(
        invoke("compatibility", package_path, "--profile", profile, "--requirements", requirements)
    )["result"]
    assert checked["compatible"] is True and checked["assurance"] == "synthetic-declaration"
    assert checked["signature_status"] == "not_checked" and checked["authority_granted"] is False
    for field, expected in (
        ("connectors", "unsupported_connector"),
        ("models", "unsupported_model"),
        ("platforms", "unsupported_platform"),
    ):
        partial = copy.deepcopy(complete)
        partial[field] = []
        profile.write_text(json.dumps(partial))
        checked = report(
            invoke(
                "compatibility", package_path, "--profile", profile, "--requirements", requirements
            ),
            3,
        )["result"]
        assert expected in {reason["code"] for reason in checked["reasons"]}
    complete[MARKER] = MARKER
    profile.write_text(json.dumps(complete))
    failure(
        invoke("compatibility", package_path, "--profile", profile, "--requirements", requirements),
        "invalid_capability_profile",
    )
    del complete[MARKER]
    profile.write_text(json.dumps(complete))
    requirements.write_text(json.dumps({MARKER: MARKER}))
    failure(
        invoke("compatibility", package_path, "--profile", profile, "--requirements", requirements),
        "invalid_compatibility_requirements",
    )


def test_help_arguments_and_capped_value_free_errors(invoke, package_path, tmp_path):
    for command in (
        (),
        ("digest",),
        ("canonicalize", package_path),
        ("sign", package_path),
        ("diff", package_path),
        ("unknown-" + MARKER,),
        ("validate", package_path, "--" + MARKER),
        ("digest", tmp_path / MARKER),
    ):
        failure(invoke(*command))
    for command in (
        (),
        ("validate",),
        ("digest",),
        ("canonicalize",),
        ("diff",),
        ("keygen",),
        ("sign",),
        ("verify",),
        ("compatibility",),
    ):
        result = invoke(*command, "--help")
        assert result.returncode == 0 and result.stderr == ""
        assert "usage:" in result.stdout
    raw = json.loads(package_path.read_bytes())
    raw.update({f"{MARKER}-{index}": MARKER for index in range(40)})
    package_path.write_text(json.dumps(raw))
    assert len(failure(invoke("validate", package_path))["errors"]) == 20


@pytest.mark.parametrize(
    "role,limit",
    (
        ("signature", 4096),
        ("key", 4096),
        ("trust", 65536),
        ("profile", 65536),
        ("requirements", 16384),
    ),
)
def test_auxiliary_boundaries(invoke, package_path, signed, tmp_path, role, limit):
    private, _, signature, trust = signed
    auxiliary = tmp_path / (MARKER + ".json")
    profile = tmp_path / "profile.json"
    requirements = tmp_path / "requirements.json"
    profile.write_bytes((EXAMPLES / "synthetic-capabilities.v1.json").read_bytes())
    requirements.write_bytes((EXAMPLES / "synthetic-requirements.v1.json").read_bytes())
    commands = {
        "key": ("sign", package_path, "--key", auxiliary, "--output", tmp_path / "new-signature"),
        "signature": ("verify", package_path, "--signature", auxiliary, "--trust", trust),
        "trust": ("verify", package_path, "--signature", signature, "--trust", auxiliary),
        "profile": (
            "compatibility",
            package_path,
            "--profile",
            auxiliary,
            "--requirements",
            requirements,
        ),
        "requirements": (
            "compatibility",
            package_path,
            "--profile",
            profile,
            "--requirements",
            auxiliary,
        ),
    }
    for data in (
        b" " * (limit + 1),
        b"{",
        b"\xff",
        b'{"x":1,"x":2}',
        b'{"x":NaN}',
        b"[" * 33 + b"0" + b"]" * 33,
        json.dumps({MARKER: MARKER}).encode(),
    ):
        auxiliary.write_bytes(data)
        auxiliary.chmod(0o600)
        failure(invoke(*commands[role]), "input_too_large" if len(data) > limit else None)
    if role in ("trust", "profile"):
        auxiliary.write_bytes(b"[" + b"0," * 10000 + b"0]")
        failure(
            invoke(*commands[role]),
            "invalid_trust_set" if role == "trust" else "invalid_capability_profile",
        )
    auxiliary.unlink()
    if hasattr(os, "mkfifo"):
        os.mkfifo(auxiliary, 0o600)
        failure(invoke(*commands[role]), "file_unreadable")
    else:
        pytest.skip("Named pipes unavailable")
    assert private.is_file()


def test_key_algorithms_permissions_symlinks_and_incomplete_pair(invoke, package_path, tmp_path):
    key = tmp_path / "key.pem"
    key.write_bytes(
        rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    key.chmod(0o600)
    failure(
        invoke("sign", package_path, "--key", key, "--output", tmp_path / "sig"),
        "invalid_signing_key",
    )
    key.chmod(0o644)
    failure(
        invoke("sign", package_path, "--key", key, "--output", tmp_path / "sig"),
        "unsafe_private_key",
    )
    key.chmod(0o600)
    link = tmp_path / "symlink"
    link.symlink_to(key)
    failure(
        invoke("sign", package_path, "--key", link, "--output", tmp_path / "sig"), "file_unreadable"
    )
    failure(invoke("keygen", "--private", key, "--public", key), "key_paths_conflict")
    partial = tmp_path / "partial-private"
    failure(
        invoke("keygen", "--private", partial, "--public", tmp_path / "absent" / "public"),
        "keypair_incomplete",
    )
    assert stat.S_IMODE(partial.stat().st_mode) == 0o600
    assert partial.read_bytes().startswith(b"-----BEGIN PRIVATE KEY-----")


def test_duplicate_trust_and_invalid_signature_are_format_failures(invoke, package_path, signed):
    _, _, signature, trust = signed
    trust.write_text('{"format_version":1,"keys":[],"keys":[]}')
    failure(
        invoke("verify", package_path, "--signature", signature, "--trust", trust), "duplicate_key"
    )
    trust.write_text('{"format_version":1,"keys":[]}')
    signature.write_text(json.dumps({MARKER: MARKER}))
    failure(
        invoke("verify", package_path, "--signature", signature, "--trust", trust),
        "invalid_signature_envelope",
    )


def test_main_returns_from_help(capsys):
    from lotuspond_sdk.cli import main

    assert main(["--help"]) == 0
    assert "usage:" in capsys.readouterr().out


def test_outer_json_report_cap_fails_safely(monkeypatch, capsys, tmp_path, golden_bytes):
    from lotuspond_sdk import cli

    path = tmp_path / "workflow.json"
    path.write_bytes(golden_bytes)
    monkeypatch.setattr(cli, "MAX_REPORT_BYTES", 200, raising=False)
    assert cli.main(["diff", str(path), str(path)]) == 2
    result = capsys.readouterr()
    assert result.err == ""
    assert json.loads(result.out)["errors"][0]["code"] == "report_too_large"


@pytest.mark.parametrize(
    "command", ("validate", "digest", "canonicalize", "diff", "sign", "verify", "compatibility")
)
def test_package_bound_applies_to_every_command(invoke, package_path, tmp_path, command):
    package_path.write_bytes(b" " * (1048576 + 1))
    options = {
        "canonicalize": ("--output", tmp_path / "out"),
        "sign": ("--key", tmp_path / MARKER, "--output", tmp_path / "out"),
        "verify": ("--signature", tmp_path / MARKER, "--trust", tmp_path / MARKER),
        "compatibility": ("--profile", tmp_path / MARKER, "--requirements", tmp_path / MARKER),
        "diff": (package_path,),
    }
    failure(invoke(command, package_path, *options.get(command, ())), "input_too_large")
    assert not (tmp_path / "out").exists()


def test_publication_failure_keeps_created_file_and_sanitizes_error(
    monkeypatch, capsys, tmp_path, golden_bytes
):
    from lotuspond_sdk import cli, files

    source, target = tmp_path / "workflow.json", tmp_path / MARKER
    source.write_bytes(golden_bytes)

    def fail_fsync(descriptor):
        raise OSError(MARKER)

    monkeypatch.setattr(files.os, "fsync", fail_fsync)
    assert cli.main(["canonicalize", str(source), "--output", str(target)]) == 2
    result = capsys.readouterr()
    assert result.err == "" and MARKER not in result.out
    assert json.loads(result.out)["errors"][0]["code"] == "output_incomplete"
    assert target.read_bytes() == golden_bytes
