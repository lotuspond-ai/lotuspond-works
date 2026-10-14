"""Install a verified image bundle into a disposable, internal-only Docker network.

This uses synthetic records only. It never connects to an existing database,
publishes a port, pulls an image, or changes an existing container or volume.
It is a local installation drill, not a clean-machine or production acceptance test.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

from verify_airgap_bundle import MAX_BUNDLE_BYTES, verify_bundle

IMAGE_KEYS = {
    "LOTUSPOND_API_IMAGE",
    "LOTUSPOND_COLLECTOR_IMAGE",
    "POSTGRES_IMAGE",
    "REDIS_IMAGE",
}
LABEL = "com.lotuspond.airgap-drill"


# Ephemeral credentials this drill generates for its own disposable containers. They are
# scrubbed from failure detail so a diagnosable error never carries one.
EPHEMERAL_SECRETS: set[str] = set()


def command(*args, data=None, timeout=60, check=True):
    result = subprocess.run(
        args, input=data, capture_output=True, timeout=timeout, check=False
    )
    if check and result.returncode:
        # Never emit command arguments, environment, auth responses, or database output.
        # The failing program's own stderr tail is included so a drill failure is
        # diagnosable without rerunning it under a patched harness.
        detail = result.stderr.decode("utf-8", "replace")[-2000:].strip()
        for secret in EPHEMERAL_SECRETS:
            if secret:
                detail = detail.replace(secret, "[redacted]")
        raise RuntimeError(
            f"{args[0]} {args[1]} failed (exit {result.returncode}): {detail}"
            if detail
            else f"{args[0]} {args[1]} failed (exit {result.returncode})"
        )
    return result


def packaged_schema_version(path: Path) -> int:
    """Read the version the bundle's own reviewable schema installs.

    The drill applies exactly this file, so the running API must report the same
    number. Reading it here keeps the check tied to the artifact instead of a
    literal that silently goes stale when the model moves on.
    """
    matches = re.findall(
        r"INSERT INTO schema_version \(version, installed_at\) VALUES \((\d{1,6}),",
        path.read_text(),
    )
    if len(matches) != 1:
        raise ValueError("the packaged schema must declare exactly one version")
    return int(matches[0])


def image_inventory(path: Path) -> dict[str, str]:
    if path.stat().st_size > 4096:
        raise ValueError("image inventory exceeds its bound")
    inventory = json.loads(path.read_text())
    if not isinstance(inventory, dict) or set(inventory) != {"schema", "images"}:
        raise ValueError("invalid image inventory")
    images = inventory["images"]
    if (
        inventory["schema"] != "lotuspond.offline-images.v1"
        or not isinstance(images, dict)
        or set(images) != IMAGE_KEYS
        or not all(
            isinstance(image, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", image)
            for image in images.values()
        )
    ):
        raise ValueError("image inventory requires exactly four immutable image IDs")
    return images


def verified_inputs(archive: Path, checksum: Path, private: Path):
    # A private copy prevents path replacement between verification and extraction.
    copied = private / "input.tar.gz"
    size = 0
    with archive.open("rb") as source, copied.open("xb") as target:
        while chunk := source.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_BUNDLE_BYTES:
                raise ValueError("bundle exceeds the compressed-size limit")
            target.write(chunk)
    proof = verify_bundle(copied, checksum)
    # Copy only three exact regular files, never archive-selected filesystem paths.
    with tarfile.open(copied, "r:gz") as bundle:
        for name, limit in (
            ("images.json", 4096),
            ("schema.sql", 1024 * 1024),
            ("images.tar", MAX_BUNDLE_BYTES),
        ):
            member = bundle.getmember(f"{proof.root}/{name}")
            if not member.isfile() or member.size > limit:
                raise ValueError("required bundle input is missing or oversized")
            source = bundle.extractfile(member)
            if source is None:
                raise ValueError("required bundle input cannot be read")
            with source, (private / name).open("xb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
    copied.unlink()
    return proof


def environment_file(directory: Path, name: str, values: dict[str, str]) -> Path:
    path = directory / name
    if any(
        "\n" in value or "\r" in value or "\0" in value for value in values.values()
    ):
        raise ValueError("invalid environment value")
    path.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
    path.chmod(0o600)
    return path


def wait_for(probe, seconds=40):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if probe():
            return
        time.sleep(0.3)
    raise RuntimeError("isolated service did not become ready before its deadline")


def owned_container_action(container: str, run: str, action: str):
    if action not in {"stop", "start"}:
        raise ValueError("unsupported owned-container action")
    inspected = json.loads(command("docker", "inspect", container).stdout)[0]
    if (
        inspected.get("Name") != "/" + container
        or inspected.get("Config", {}).get("Labels", {}).get(LABEL) != run
    ):
        raise RuntimeError("container ownership check failed")
    args = ["docker", "container", action]
    if action == "stop":
        args += ["--time", "10"]
    command(*args, container, timeout=20)


SETUP = r"""
import base64, json, pathlib, subprocess, sys, tempfile, time, urllib.request, urllib.error, urllib.parse
from cryptography.hazmat.primitives.hashes import SHA1
from cryptography.hazmat.primitives.twofactor.totp import TOTP
data = json.load(sys.stdin)
origin = "https://lotuspond.airgap.invalid"
headers = {"Content-Type": "application/json", "Origin": origin}
def request(path, body=None, expected=200):
    req = urllib.request.Request("http://127.0.0.1:8000/api/v1" + path,
        data=json.dumps(body).encode() if body is not None else None, headers=headers)
    try:
        response = urllib.request.urlopen(req, timeout=5)
    except urllib.error.HTTPError as error:
        response = error
    assert response.status == expected, (path, response.status)
    content = json.load(response)
    return content, response.headers
registration, response_headers = request("/auth/register", {
    "name": "Offline installation drill", "email": "offline-drill@example.com",
    "password": data["password"], "account_type": "individual"}, 201)
cookie = response_headers["Set-Cookie"]
assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie
headers["Cookie"] = cookie.split(";", 1)[0]
headers["x-lotuspond-workspace"] = registration["workspaceId"]
me, _ = request("/me")
assert me["capabilities"]["mail_enabled"] is False
denied, _ = request("/organizations", {"name": "Cannot bypass offline identity verification"}, 403)
assert denied["error"] == "identity_unverified"
request("/auth/logout", {})
headers.pop("Cookie")
headers.pop("x-lotuspond-workspace")
with tempfile.TemporaryDirectory(prefix="offline-enrollment-") as temporary:
    token_file = pathlib.Path(temporary) / "enrollment.txt"
    command = [sys.executable, "-m", "lotuspond.offline_provision", "provision",
        "--email", "attested-offline-drill@example.com", "--name", "Synthetic offline operator",
        "--attestation-source", "in_person", "--source-reference", "synthetic-drill-only",
        "--operator-reference", "isolated-installation-drill", "--organization-name", "Offline synthetic organization",
        "--project", "Offline synthetic project", "--platform-admin", "--expires-hours", "1",
        "--token-file", str(token_file), "--confirm", "PROVISION NEW OFFLINE IDENTITY"]
    result = subprocess.run(command, capture_output=True, timeout=15)
    assert result.returncode == 0, "offline provisioning command failed"
    assert token_file.stat().st_mode & 0o777 == 0o600
    fragment = urllib.parse.urlsplit(token_file.read_text().strip()).fragment
    token = urllib.parse.parse_qs(fragment.split("?", 1)[1])["token"][0]
    assert token.encode() not in result.stdout + result.stderr
    token_file.unlink()
request("/auth/login", {"email": "attested-offline-drill@example.com", "password": data["password"]}, 401)
inspected, _ = request("/auth/offline-enrollment/inspect", {"token": token})
assert inspected["identity_verification_source"] == "operator_attested"
assert inspected["organization"]["name"] == "Offline synthetic organization"
assert len(inspected["projects"]) == 1
completed, completion_headers = request("/auth/offline-enrollment/complete", {"token": token, "password": data["password"]})
assert "Max-Age=0" in completion_headers.get("Set-Cookie", "")
request("/me", expected=401)
request("/auth/offline-enrollment/complete", {"token": token, "password": data["password"]}, 409)
_, login_headers = request("/auth/login", {"email": "attested-offline-drill@example.com", "password": data["password"]})
cookie = login_headers["Set-Cookie"]
assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie
headers["Cookie"] = cookie.split(";", 1)[0]
headers["x-lotuspond-workspace"] = inspected["organization"]["id"]
me, _ = request("/me")
assert me["user"]["email_verified"] is False
assert me["user"]["identity_verified"] is True
assert me["user"]["identity_verification_source"] == "operator_attested"
assert {row["kind"] for row in me["workspaces"]} == {"personal", "organization"}
assert me["mfa"]["available"] and me["mfa"]["required_for_privileged"]
assert not me["mfa"]["enabled"] and not me["mfa"]["session_verified"]
denied, _ = request("/admin/overview", expected=403)
assert denied["error"] == "mfa_required"
ordinary_cookie = headers["Cookie"]
setup, setup_headers = request("/auth/mfa/enrollment/start", {"current_password": data["password"]})
headers["Cookie"] = setup_headers["Set-Cookie"].split(";", 1)[0]
secret = base64.b32decode(setup["secret"])
authenticator = TOTP(secret, 6, SHA1(), 30)
enrollment_step = int(time.time()) // 30
first_code = authenticator.generate(enrollment_step * 30).decode()
enabled, enabled_headers = request("/auth/mfa/enrollment/confirm", {"code": first_code})
assert len(enabled["recovery_codes"]) == 10
verified_cookie = enabled_headers["Set-Cookie"].split(";", 1)[0]
headers["Cookie"] = ordinary_cookie
request("/me", expected=401)
headers["Cookie"] = verified_cookie
request("/admin/overview")
request("/auth/mfa/disable", {"current_password": data["password"], "code": enabled["recovery_codes"][0]}, 403)
request("/auth/logout", {})
headers.pop("Cookie")
challenge, challenge_headers = request("/auth/login", {
    "email": "attested-offline-drill@example.com", "password": data["password"]}, 202)
assert challenge["mfa_required"] is True
assert not challenge_headers.get("Set-Cookie") or "Max-Age=0" in challenge_headers["Set-Cookie"]
request("/me", expected=401)
request("/auth/mfa/challenge/complete", {"challenge_token": challenge["challenge_token"], "code": first_code}, 400)
while int(time.time()) // 30 <= enrollment_step:
    time.sleep(0.1)
code = authenticator.generate(int(time.time())).decode()
_, verified_headers = request("/auth/mfa/challenge/complete", {
    "challenge_token": challenge["challenge_token"], "code": code})
headers["Cookie"] = verified_headers["Set-Cookie"].split(";", 1)[0]
request("/auth/mfa/challenge/complete", {"challenge_token": challenge["challenge_token"], "code": code}, 400)
request("/admin/overview")
request("/auth/logout", {})
headers.pop("Cookie")
challenge, _ = request("/auth/login", {
    "email": "attested-offline-drill@example.com", "password": data["password"]}, 202)
_, recovered_headers = request("/auth/mfa/challenge/complete", {
    "challenge_token": challenge["challenge_token"], "code": enabled["recovery_codes"][0]})
headers["Cookie"] = recovered_headers["Set-Cookie"].split(";", 1)[0]
request("/auth/logout", {})
headers.pop("Cookie")
challenge, _ = request("/auth/login", {
    "email": "attested-offline-drill@example.com", "password": data["password"]}, 202)
request("/auth/mfa/challenge/complete", {
    "challenge_token": challenge["challenge_token"], "code": enabled["recovery_codes"][0]}, 400)
_, recovered_headers = request("/auth/mfa/challenge/complete", {
    "challenge_token": challenge["challenge_token"], "code": enabled["recovery_codes"][1]})
headers["Cookie"] = recovered_headers["Set-Cookie"].split(";", 1)[0]
me, _ = request("/me")
assert me["mfa"]["enabled"] and me["mfa"]["session_verified"]
assert me["mfa"]["recovery_codes_remaining"] == 8
request("/admin/overview")
projects, _ = request("/projects")
project = next(row for row in projects["items"] if row["id"] == inspected["projects"][0]["id"])
request("/monitors", {"name": "Offline output assertion", "project_id": project["id"],
    "criteria": "Match the expected response", "sample_basis_points": 10000}, 201)
failed, _ = request("/traces", {"project_id": project["id"], "external_id": "offline-failure",
    "name": "Synthetic assertion failure", "status": "ok", "latency_ms": 1,
    "attributes": {"output": "different", "expected": {"equals": "required"}}}, 201)
unknown, _ = request("/traces", {"project_id": project["id"], "external_id": "offline-semantic",
    "name": "Synthetic semantic request", "status": "ok", "latency_ms": 1,
    "attributes": {"output": "Requires a semantic evaluator"}}, 201)
assert failed["cost_usd"] is None and failed["cost_measurement"] == "unavailable"
zero, _ = request("/traces", {"project_id": project["id"], "external_id": "offline-reported-zero",
    "name": "Explicit zero cost", "status": "ok", "latency_ms": 1, "cost_usd": 0,
    "attributes": {"output": "match", "expected": {"equals": "match"}}}, 201)
assert float(zero["cost_usd"]) == 0 and zero["cost_measurement"] == "reported"
key, _ = request("/api-keys", {"name": "Disposable collector", "scopes": ["ingest"], "days": 1}, 201)
print(json.dumps({"headers": headers, "project_id": project["id"], "api_key": key["key"],
    "failed_trace_id": failed["id"], "unknown_trace_id": unknown["id"],
    "user_id": me["user"]["id"]}))
"""

VERIFY = r"""
import json, sys, time, urllib.request, urllib.error
data = json.load(sys.stdin)
def request(url, headers, body=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
        headers=headers)
    with urllib.request.urlopen(req, timeout=5) as response:
        return response.status, json.load(response)
def text_request(url, headers):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=5) as response:
        return response.status, response.read(262144).decode()
def binary_request(url, headers, body):
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=5) as response:
        return response.status, response.headers.get("Content-Type"), response.read(65536)
def varint(value):
    out = bytearray()
    while value > 0x7F:
        out.append(value & 0x7F | 0x80)
        value >>= 7
    return bytes(out + bytes([value]))
def field(number, payload):
    return varint(number << 3 | 2) + varint(len(payload)) + payload
def fixed64(number, value):
    return varint(number << 3 | 1) + value.to_bytes(8, "little")
base = "http://127.0.0.1:8000/api/v1"
deadline = time.monotonic() + 25
while time.monotonic() < deadline:
    _, failed = request(base + "/traces/" + data["failed_trace_id"], data["headers"])
    _, unknown = request(base + "/traces/" + data["unknown_trace_id"], data["headers"])
    if failed["assessments"] and unknown["assessments"]:
        break
    time.sleep(0.25)
assert len(failed["assessments"]) == len(unknown["assessments"]) == 1
assert failed["assessments"][0]["status"] == "failed"
assert unknown["assessments"][0]["status"] == "unknown"
_, incidents = request(base + "/incidents", data["headers"])
categories = {item["category"] for item in incidents["items"]}
assert {"assessment_failed", "assessment_unavailable"} <= categories
for incident in incidents["items"]:
    _, detail = request(base + "/incidents/" + incident["id"], data["headers"])
    assert detail["alerts"] == []
    assert detail["history"]
collector = "http://127.0.0.1:4318"
assert request(collector + "/healthz", {})[0] == 200
try:
    request(collector + "/v1/traces", {"Content-Type": "application/json"}, {"resourceSpans": []})
    raise AssertionError("unauthenticated collector accepted telemetry")
except urllib.error.HTTPError as error:
    assert error.code == 401
status, _ = request(collector + "/v1/traces", {
    "Content-Type": "application/json", "Authorization": "Bearer " + data["ingest_token"]}, {
    "resourceSpans": [{"scopeSpans": [{"spans": [{
        "traceId": "a" * 32, "spanId": "b" * 16, "name": "Offline collector span",
        "startTimeUnixNano": "1000000000", "endTimeUnixNano": "1001000000",
        "status": {"code": 1}, "attributes": []}]}]}]})
assert status in (200, 202)
# The OpenTelemetry SDK default: a binary ExportTraceServiceRequest, answered in kind.
span = (field(1, bytes([0xCC]) * 16) + field(2, bytes([0xDD]) * 8) + field(5, b"Offline protobuf span")
    + fixed64(7, 1_000_000_000) + fixed64(8, 1_002_000_000))
status, content_type, answer = binary_request(collector + "/v1/traces", {
    "Content-Type": "application/x-protobuf", "Authorization": "Bearer " + data["ingest_token"]},
    field(1, field(2, field(2, span))))
assert status == 200 and content_type == "application/x-protobuf" and answer == b""
for token in (None, data["ingest_token"]):
    headers_for_metrics = {} if token is None else {"Authorization": "Bearer " + token}
    try:
        text_request(collector + "/metrics", headers_for_metrics)
        raise AssertionError("collector metrics accepted the wrong credential")
    except urllib.error.HTTPError as error:
        assert error.code == 401
metrics_status, metrics = text_request(collector + "/metrics", {
    "Authorization": "Bearer " + data["metrics_token"]})
assert metrics_status == 200
for series in (
    'lotuspond_collector_requests_total{result="accepted"}',
    "lotuspond_collector_request_duration_seconds_count",
    "lotuspond_collector_forward_duration_seconds_count",
    "lotuspond_collector_in_flight", "lotuspond_collector_queue_depth",
    "lotuspond_collector_queue_capacity", "lotuspond_collector_workers",
    "lotuspond_collector_uptime_seconds",
    # The spool is enabled on the packaged /spool; delivered batches have left it.
    "lotuspond_collector_spool_pending_batches 0", "lotuspond_collector_spool_bytes 0",
    'lotuspond_collector_spool_replays_total{outcome="delivered"}'):
    assert series in metrics
for forbidden in (data["metrics_token"], data["ingest_token"], data["api_key"],
        data["project_id"], "api/v1/telemetry/otlp"):
    assert forbidden not in metrics
_, traces = request(base + "/traces?project_id=" + data["project_id"], data["headers"])
names = {trace["name"] for trace in traces["items"]}
assert {"Offline collector span", "Offline protobuf span"} <= names
_, ready = request(base + "/ready", {})
assert ready["schema_version"] == data["schema_version"]
with urllib.request.urlopen("http://127.0.0.1:8000/", timeout=5) as response:
    assert response.status == 200 and "Lotuspond" in response.read(65536).decode()
print(json.dumps({"schema_version": ready["schema_version"],
    "deterministic_failure": True, "semantic_unknown": True,
    "incident_categories": sorted(categories), "alerts_disabled": True,
    "collector_authenticated_ingestion": True, "collector_protobuf_ingestion": True,
    "collector_spool_enabled": True, "production_identity_gate": True,
    "collector_metrics_separate_auth": True,
    "collector_metrics_fixed_nonsecret_series": True,
    "collector_metrics_text": metrics,
    "offline_operator_enrollment": True, "mailbox_remains_unverified": True,
    "enrollment_replay_rejected": True, "normal_login_required": True,
    "production_operator_requires_mfa": True, "mfa_totp_login_and_replay_denial": True,
    "mfa_recovery_codes_single_use": True, "mfa_enrollment_revokes_old_sessions": True,
    "production_operator_mfa_disable_denied": True,
    "trace_cost_null_and_reported_zero": True}))
"""


FAILURE_ENQUEUE = r"""
import json, sys, urllib.request
data = json.load(sys.stdin)
base = "http://127.0.0.1:8000/api/v1"
def request(path, body=None):
    req = urllib.request.Request(base + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers=data["headers"])
    with urllib.request.urlopen(req, timeout=5) as response:
        return json.load(response)
trace = request("/traces", {"project_id": data["project_id"],
    "external_id": "offline-worker-recovery", "name": "Worker recovery evidence",
    "status": "ok", "latency_ms": 1,
    "attributes": {"output": "queued", "expected": {"equals": "processed"}}})
operations = request("/admin/operations")
assert operations["queues"]["evaluation"]["eligible_now"] >= 1
print(json.dumps({"trace_id": trace["id"], "backlog_eligible": True}))
"""


FAILURE_OBSERVE = r"""
import json, sys, time, urllib.request, urllib.error
data = json.load(sys.stdin)
base = "http://127.0.0.1:8000/api/v1"
def request(path):
    req = urllib.request.Request(base + path, headers=data["headers"])
    try:
        response = urllib.request.urlopen(req, timeout=5)
    except urllib.error.HTTPError as error:
        response = error
    content = json.load(response)
    return response.status, content
deadline = time.monotonic() + data["timeout_seconds"]
while time.monotonic() < deadline:
    status, operations = request("/admin/operations")
    if data["mode"] == "stale":
        observation = operations["workers"]["evaluation"]["observation"]
        ready = (status == 200 and operations["queues"]["evaluation"]["eligible_now"] >= 1
            and observation["state"] == "stale" and observation["age_seconds"] > 150)
    elif data["mode"] == "drained":
        observation = operations["workers"]["evaluation"]["observation"]
        _, trace = request("/traces/" + data["trace_id"])
        ready = (status == 200 and operations["queues"]["evaluation"]["eligible_now"] == 0
            and observation["state"] == "fresh" and len(trace["assessments"]) == 1)
    elif data["mode"] == "redis_unavailable":
        ready = (status == 200 and operations["dependencies"]["redis"] == {
            "state": "unavailable", "error_code": "redis_unavailable"})
    elif data["mode"] == "redis_recovered":
        observation = operations["workers"]["evaluation"]["observation"]
        ready = (status == 200 and operations["dependencies"]["redis"]["state"] == "ok"
            and observation["state"] == "fresh")
    else:
        raise AssertionError("unknown observation mode")
    if ready:
        print(json.dumps({"mode": data["mode"], "observed": True}))
        break
    time.sleep(0.5)
else:
    raise AssertionError("failure-recovery observation deadline exceeded")
"""


REVOKE = r"""
import json, subprocess, sys, urllib.request, urllib.error
from sqlalchemy import select
from lotuspond.config import Settings
from lotuspond.database import engine_for, sessions_for
from lotuspond.models import AuditEvent, OfflineIdentityAttestation, Project, Trace, User
data = json.load(sys.stdin)
result = subprocess.run([sys.executable, "-m", "lotuspond.offline_provision", "revoke-identity",
    "--user-id", data["user_id"], "--email", "attested-offline-drill@example.com",
    "--operator-reference", "isolated-installation-drill", "--reason", "Synthetic drill complete",
    "--confirm", "REVOKE OFFLINE IDENTITY"], capture_output=True, timeout=15)
assert result.returncode == 0, "offline revocation command failed"
for path, headers in (
    ("/admin/overview", data["headers"]),
    ("/traces", {"Authorization": "Bearer " + data["api_key"]}),
):
    request = urllib.request.Request("http://127.0.0.1:8000/api/v1" + path, headers=headers)
    try:
        urllib.request.urlopen(request, timeout=5)
        raise AssertionError("revoked identity still authorized")
    except urllib.error.HTTPError as error:
        assert error.code == 401
engine = engine_for(Settings())
try:
    with sessions_for(engine)() as db:
        assert db.get(User, data["user_id"]).disabled_at is not None
        assert db.get(Project, data["project_id"]) is not None
        assert db.get(Trace, data["failed_trace_id"]) is not None
        assert db.scalar(select(OfflineIdentityAttestation).where(
            OfflineIdentityAttestation.user_id == data["user_id"])).revoked_at is not None
        assert db.scalar(select(AuditEvent.id).where(
            AuditEvent.resource_id == data["user_id"],
            AuditEvent.action == "account.offline_identity_revoked")) is not None
finally:
    engine.dispose()
print(json.dumps({"offline_revocation_blocks_sessions_and_keys": True,
    "offline_revocation_retains_business_data": True}))
"""


def drill(archive: Path, checksum: Path, *, failure_recovery: bool = False) -> dict:
    run = secrets.token_hex(8)
    prefix = "lotuspond-offline-" + run
    network = prefix + "-net"
    attempted = []
    network_attempted = False
    report = {}
    cleanup_errors = []
    anonymous_volumes = set()
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix=prefix + "-") as temporary:
        private = Path(temporary)
        private.chmod(0o700)
        proof = verified_inputs(archive, checksum, private)
        images = image_inventory(private / "images.json")
        command(
            "docker",
            "image",
            "load",
            "--input",
            str(private / "images.tar"),
            timeout=300,
        )
        for image in images.values():
            actual = command("docker", "image", "inspect", "--format", "{{.Id}}", image)
            if actual.stdout.decode().strip() != image:
                raise RuntimeError("loaded image does not match the bundle inventory")

        def launch(name, image, env, args=(), app=False, shared_network=None, mounts=()):
            container = prefix + "-" + name
            attempted.append(container)
            flags = [
                "docker",
                "run",
                "--detach",
                "--pull=never",
                "--name",
                container,
                "--label",
                f"{LABEL}={run}",
                "--network",
                shared_network or network,
                "--env-file",
                str(environment_file(private, name + ".env", env)),
                "--security-opt=no-new-privileges:true",
                "--pids-limit=128",
                "--memory=512m",
            ]
            if app:
                flags += [
                    "--read-only",
                    "--cap-drop=ALL",
                    "--tmpfs",
                    "/tmp:size=64m,mode=1777",
                ]
            for mount in mounts:
                # Anonymous volumes only; they are inventoried and removed with the drill.
                flags += ["--mount", mount]
            command(*flags, image, *args)
            return container

        def execute(container, program, data=None, timeout=60):
            return command(
                "docker",
                "exec",
                "-i",
                container,
                "python",
                "-c",
                program,
                data=json.dumps(data).encode() if data is not None else None,
                timeout=timeout,
            )

        try:
            network_attempted = True
            command(
                "docker",
                "network",
                "create",
                "--internal",
                "--label",
                f"{LABEL}={run}",
                network,
            )
            inspected = json.loads(
                command("docker", "network", "inspect", network).stdout
            )[0]
            assert (
                inspected["Internal"] is True and inspected["Labels"].get(LABEL) == run
            )
            database_password, redis_password = (
                secrets.token_hex(24),
                secrets.token_hex(24),
            )
            postgres = launch(
                "postgres",
                images["POSTGRES_IMAGE"],
                {
                    "POSTGRES_USER": "lotuspond",
                    "POSTGRES_DB": "lotuspond_offline_drill",
                    "POSTGRES_PASSWORD": database_password,
                },
            )
            redis = launch(
                "redis",
                images["REDIS_IMAGE"],
                {"REDIS_PASSWORD": redis_password},
                ("sh", "-c", 'exec redis-server --requirepass "$REDIS_PASSWORD"'),
            )
            wait_for(
                lambda: (
                    command(
                        "docker",
                        "exec",
                        postgres,
                        "sh",
                        "-c",
                        'PGPASSWORD="$POSTGRES_PASSWORD" psql -h 127.0.0.1 -X -A -t '
                        '-U lotuspond -d lotuspond_offline_drill -c "SELECT 1"',
                        check=False,
                    ).returncode
                    == 0
                )
            )
            command(
                "docker",
                "exec",
                "-i",
                postgres,
                "psql",
                "-X",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                "lotuspond",
                "-d",
                "lotuspond_offline_drill",
                data=(private / "schema.sql").read_bytes(),
            )
            config = {
                "LOTUSPOND_ENV": "production",
                "LOTUSPOND_ORIGIN": "https://lotuspond.airgap.invalid",
                "LOTUSPOND_NETWORK_MODE": "airgap",
                "LOTUSPOND_MAIL_ENABLED": "false",
                "LOTUSPOND_ALERT_WEBHOOKS": "[]",
                "LOTUSPOND_REGISTRATION_ENABLED": "true",
                "LOTUSPOND_MFA_ENCRYPTION_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
                "LOTUSPOND_DATABASE_URL": f"postgresql+psycopg://lotuspond:{database_password}@{postgres}:5432/lotuspond_offline_drill",
                "LOTUSPOND_REDIS_URL": f"redis://:{redis_password}@{redis}:6379/0",
            }
            api = launch("api", images["LOTUSPOND_API_IMAGE"], config, app=True)
            probe = "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/ready',timeout=2).read()"
            wait_for(
                lambda: (
                    command(
                        "docker",
                        "exec",
                        api,
                        "python",
                        "-c",
                        probe,
                        check=False,
                        timeout=5,
                    ).returncode
                    == 0
                )
            )
            sdk_probe = r"""
import json, sys
from pathlib import Path
import lotuspond_sdk
from lotuspond import package_contract, package_input
from lotuspond_sdk import input as sdk_input
assert package_contract.WorkflowPackage is lotuspond_sdk.WorkflowPackage
assert package_input.PackageReadError is sdk_input.PackageReadError
assert Path(lotuspond_sdk.__file__).is_relative_to(Path(sys.prefix))
assert not Path('/app/sdk/python').exists()
package = lotuspond_sdk.validate_package(json.loads(sys.stdin.read()))
assert lotuspond_sdk.package_digest(package) == '305efc96011ba5fc2f527cefbe416890b0265269e18e3402354ffbfed40251d3'
"""
            command(
                "docker", "exec", "-i", api, "python", "-c", sdk_probe,
                data=(Path(__file__).resolve().parents[2]
                      / "contracts/examples/reviewed-action.v1.json").read_bytes(),
            )
            drill_password = secrets.token_urlsafe(24)
            EPHEMERAL_SECRETS.add(drill_password)
            context = json.loads(
                execute(api, SETUP, {"password": drill_password}).stdout
            )
            context["ingest_token"] = secrets.token_urlsafe(32)
            context["metrics_token"] = secrets.token_urlsafe(32)
            context["schema_version"] = packaged_schema_version(private / "schema.sql")
            EPHEMERAL_SECRETS.update(
                value
                for key, value in context.items()
                if isinstance(value, str) and key.endswith(("token", "key", "password"))
            )
            collector = launch(
                "collector",
                images["LOTUSPOND_COLLECTOR_IMAGE"],
                {
                    "COLLECTOR_INGEST_TOKEN": context["ingest_token"],
                    "COLLECTOR_METRICS_TOKEN": context["metrics_token"],
                    "LOTUSPOND_API_KEY": context["api_key"],
                    "LOTUSPOND_PROJECT_ID": context["project_id"],
                    "LOTUSPOND_API_URL": "http://127.0.0.1:8000",
                    "COLLECTOR_SPOOL_DIR": "/spool",
                },
                app=True,
                shared_network="container:" + api,
                mounts=("type=volume,destination=/spool",),
            )
            worker = launch(
                "worker",
                images["LOTUSPOND_API_IMAGE"],
                {key: value for key, value in config.items() if key != "LOTUSPOND_MFA_ENCRYPTION_KEY"},
                ("python", "-m", "lotuspond.worker"),
                app=True,
            )
            report = json.loads(execute(api, VERIFY, context).stdout)
            report["standalone_sdk_identity_and_digest"] = True
            if failure_recovery:
                owned_container_action(worker, run, "stop")
                recovery = json.loads(execute(api, FAILURE_ENQUEUE, context).stdout)
                stale = json.loads(
                    execute(
                        api,
                        FAILURE_OBSERVE,
                        {
                            **context,
                            **recovery,
                            "mode": "stale",
                            "timeout_seconds": 165,
                        },
                        timeout=175,
                    ).stdout
                )
                owned_container_action(worker, run, "start")
                drained = json.loads(
                    execute(
                        api,
                        FAILURE_OBSERVE,
                        {
                            **context,
                            **recovery,
                            "mode": "drained",
                            "timeout_seconds": 25,
                        },
                        timeout=35,
                    ).stdout
                )
                owned_container_action(redis, run, "stop")
                unavailable_ready = command(
                    "docker", "exec", api, "python", "-c", probe, check=False, timeout=5
                )
                assert unavailable_ready.returncode != 0
                unavailable = json.loads(
                    execute(
                        api,
                        FAILURE_OBSERVE,
                        {**context, "mode": "redis_unavailable", "timeout_seconds": 10},
                        timeout=20,
                    ).stdout
                )
                owned_container_action(redis, run, "start")
                wait_for(
                    lambda: command(
                        "docker", "exec", api, "python", "-c", probe,
                        check=False, timeout=5,
                    ).returncode == 0,
                    seconds=20,
                )
                recovered = json.loads(
                    execute(
                        api,
                        FAILURE_OBSERVE,
                        {**context, "mode": "redis_recovered", "timeout_seconds": 25},
                        timeout=35,
                    ).stdout
                )
                assert all(item["observed"] for item in (stale, drained, unavailable, recovered))
                report.update(
                    {
                        "failure_recovery_drill": True,
                        "worker_backlog_observed": recovery["backlog_eligible"],
                        "worker_stale_observed": True,
                        "worker_restart_drained_backlog": True,
                        "redis_outage_ready_failed": True,
                        "redis_outage_operations_visible": True,
                        "redis_restart_recovered": True,
                    }
                )
            report.update(json.loads(execute(api, REVOKE, context).stdout))
            for container in (postgres, redis, api, collector, worker):
                state = json.loads(command("docker", "inspect", container).stdout)[0]
                assert (
                    state["State"]["Running"]
                    and not state["HostConfig"]["PortBindings"]
                )
                assert not state["HostConfig"]["Binds"]
            report.update(
                {
                    "archive_sha256": proof.archive_sha256,
                    "images": images,
                    "internal_network": True,
                    "published_ports": 0,
                    "synthetic_data_only": True,
                    "containers": 5,
                    "schema_sha256": hashlib.sha256(
                        (private / "schema.sql").read_bytes()
                    ).hexdigest(),
                }
            )
        finally:
            for container in reversed(attempted):
                try:
                    found = command(
                        "docker",
                        "container",
                        "ls",
                        "--all",
                        "--filter",
                        f"name=^/{container}$",
                        "--format",
                        "{{.Names}}",
                    )
                    if found.stdout.decode().strip():
                        inspected = json.loads(
                            command("docker", "inspect", container).stdout
                        )[0]
                        if inspected["Config"]["Labels"].get(LABEL) != run:
                            raise RuntimeError("cleanup label mismatch")
                        anonymous_volumes.update(
                            mount["Name"]
                            for mount in inspected.get("Mounts", [])
                            if mount["Type"] == "volume"
                        )
                        command(
                            "docker",
                            "container",
                            "rm",
                            "--force",
                            "--volumes",
                            container,
                        )
                except (
                    OSError,
                    subprocess.SubprocessError,
                    RuntimeError,
                    ValueError,
                    KeyError,
                ):
                    cleanup_errors.append(container)
            if network_attempted:
                try:
                    found = command(
                        "docker",
                        "network",
                        "ls",
                        "--filter",
                        f"name=^{network}$",
                        "--format",
                        "{{.Name}}",
                    )
                    if found.stdout.decode().strip():
                        inspected = json.loads(
                            command("docker", "network", "inspect", network).stdout
                        )[0]
                        if inspected["Labels"].get(LABEL) != run:
                            raise RuntimeError("cleanup network label mismatch")
                        command("docker", "network", "rm", network)
                    remaining = command(
                        "docker",
                        "container",
                        "ls",
                        "--all",
                        "--filter",
                        f"label={LABEL}={run}",
                        "--format",
                        "{{.Names}}",
                    )
                    networks = command(
                        "docker",
                        "network",
                        "ls",
                        "--filter",
                        f"label={LABEL}={run}",
                        "--format",
                        "{{.Name}}",
                    )
                    if remaining.stdout.strip() or networks.stdout.strip():
                        cleanup_errors.append("unconfirmed absence")
                except (
                    OSError,
                    subprocess.SubprocessError,
                    RuntimeError,
                    ValueError,
                    KeyError,
                ):
                    cleanup_errors.append(network)
            for volume in anonymous_volumes:
                try:
                    remaining = command(
                        "docker",
                        "volume",
                        "ls",
                        "--filter",
                        f"name=^{volume}$",
                        "--format",
                        "{{.Name}}",
                    )
                    if remaining.stdout.strip():
                        cleanup_errors.append(volume)
                except (OSError, subprocess.SubprocessError, RuntimeError):
                    cleanup_errors.append(volume)
            if cleanup_errors:
                message = "drill cleanup could not be confirmed: " + ", ".join(
                    cleanup_errors
                )
                if active_error := sys.exception():
                    active_error.add_note(message)
                else:
                    raise RuntimeError(message)
    report.update(
        {
            "cleanup_confirmed": True,
            "anonymous_volumes_removed": len(anonymous_volumes),
            "elapsed_seconds": round(time.monotonic() - started, 1),
        }
    )
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument(
        "checksum", type=Path, help="SHA-256 delivered through a trusted channel"
    )
    parser.add_argument(
        "--failure-recovery",
        action="store_true",
        help="also prove owned worker and Redis failure/recovery behavior",
    )
    args = parser.parse_args()
    os.umask(0o077)
    print(
        json.dumps(
            drill(args.archive, args.checksum, failure_recovery=args.failure_recovery),
            indent=2,
        )
    )
