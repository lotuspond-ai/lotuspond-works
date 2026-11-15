import hashlib
import importlib
import json
import subprocess

import pytest
from conftest import artifact_run
from test_state import C


def cli_module():
    assert importlib.util.find_spec("lotuspond_runtime.cli"), "missing private CLI"
    return importlib.import_module("lotuspond_runtime.cli")


def config_file(tmp_path, **extra):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            dict(
                origin="https://localhost",
                ca_file=str(tmp_path / "ca.pem"),
                state_directory=str(tmp_path / "state"),
                installation_id="test-install",
                observation=dict(platform="linux", connectors=[]),
                **extra,
            )
        )
    )
    path.chmod(0o600)
    return path


def test_cli_strict_private_input(tmp_path, capsys):
    cli = cli_module()
    config = config_file(tmp_path)
    token = tmp_path / "token"
    token.write_text("lpe_" + "A" * 43)
    token.chmod(0o644)
    assert cli.main(["enroll", "--config", str(config), "--token-file", str(token)]) == 4
    assert C not in "".join(capsys.readouterr())
    config_file(tmp_path, unexpected="bad")
    assert cli.main(["run", "--config", str(config)]) == 2
    assert cli.main(["enroll", "--config", str(config), "--token", C]) == 2


def test_installed_cli_outside_checkout(installed_runtime, tmp_path):
    venv, env = installed_runtime
    script = venv / "bin/lotuspond-runtime"
    assert script.exists(), "missing installed console script"
    result = subprocess.run(
        [str(script), "--help"], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0
    result = subprocess.run(
        [
            str(venv / "bin/python"),
            "-I",
            "-m",
            "lotuspond_runtime",
            "run",
            "--config",
            str(tmp_path / "missing"),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 4
    assert "Traceback" not in result.stderr
    artifact_run(
        [
            str(venv / "bin/python"),
            "-I",
            "-c",
            "import lotuspond_runtime.cli as c,sys; from pathlib import Path; "
            "assert Path(c.__file__).is_relative_to(Path(sys.prefix)); "
            'assert "lotuspond" not in sys.modules',
        ],
        tmp_path,
        env,
    )


@pytest.mark.parametrize(
    "content", ["lpe_" + "A" * 43 + " extra", "lpr_" + "A" * 43, "lpe_" + "A" * 43 + "x" * 90]
)
def test_enrollment_token_file_rejects_extra_and_wrong_type(tmp_path, content):
    from lotuspond_runtime.errors import FleetError

    token = tmp_path / "token"
    token.write_text(content)
    token.chmod(0o600)
    with pytest.raises(FleetError):
        cli_module()._token(str(token))


def test_stdin_requires_noninteractive_bounded_single_token(monkeypatch):
    import io

    from lotuspond_runtime.errors import FleetError

    class Input:
        def __init__(self, content, tty=False):
            self.buffer, self.tty = io.BytesIO(content), tty

        def isatty(self):
            return self.tty

    cli = cli_module()
    monkeypatch.setattr(cli.sys, "stdin", Input(b"lpe_" + b"A" * 43 + b"\n"))
    assert cli._token("-").get_secret_value() == "lpe_" + "A" * 43
    for content, tty in (
        (b"lpe_" + b"A" * 43, True),
        (b"x" * 129, False),
        (b"lpe_" + b"A" * 43 + b" bad", False),
    ):
        monkeypatch.setattr(cli.sys, "stdin", Input(content, tty))
        with pytest.raises(FleetError):
            cli._token("-")


@pytest.mark.parametrize("field", ["state_directory", "ca_file"])
def test_config_control_characters_fail_safely(tmp_path, capsys, field):
    config = config_file(tmp_path)
    value = json.loads(config.read_bytes())
    value[field] = "/tmp/invalid\x00path"
    config.write_text(json.dumps(value))
    assert cli_module().main(["run", "--config", str(config)]) == 2
    assert "Traceback" not in "".join(capsys.readouterr())


def test_installed_private_enroll_run_secrecy_and_shutdown(installed_runtime, tmp_path):
    import os
    import signal
    import time
    from pathlib import Path

    from test_client import IDENTITY, SESSION, TOKEN
    from tls_fixtures import tls_server

    venv, env = installed_runtime
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    enrollment = "lpe_" + "A" * 43

    def response(request):
        now = time.time_ns() // 1000000
        safe = dict(
            identity=IDENTITY,
            credential_expires_at_ms=now + 900000,
            session_expires_at_ms=None,
            server_time_ms=now,
        )
        route = request.split(b" ")[1]
        status = "200 OK"
        if route.endswith(b"/enroll"):
            payload, status = dict(token=TOKEN.get_secret_value(), state=safe), "201 Created"
        elif route.endswith(b"/session"):
            payload, status = (
                dict(
                    secret=SESSION.get_secret_value(),
                    identity=IDENTITY,
                    expires_at_ms=now + 60000,
                    server_time_ms=now,
                ),
                "201 Created",
            )
        else:
            payload = safe
        body = json.dumps(payload).encode()
        return f"HTTP/1.1 {status}\r\nContent-Length: {len(body)}\r\n\r\n".encode() + body

    with tls_server(tmp_path, response) as (origin, ca, requests):
        config = config_file(tmp_path)
        value = json.loads(config.read_bytes())
        value["origin"], value["ca_file"] = origin, str(ca)
        config.write_text(json.dumps(value))
        token = tmp_path / "enrollment-token"
        token.write_text(enrollment + "\n")
        token.chmod(0o600)
        script = str(venv / "bin/lotuspond-runtime")
        result = subprocess.run(
            [script, "enroll", "--config", str(config), "--token-file", str(token)],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, result.stderr
        assert "enrolled instance=" in result.stdout
        first_bytes = (state / "state.json").read_bytes()
        # Re-enrollment must refuse before consuming another one-time token.
        repeat = subprocess.run(
            [script, "enroll", "--config", str(config), "--token-file", str(token)],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert repeat.returncode == 4
        assert len(requests) == 1
        process = subprocess.Popen(
            [script, "run", "--config", str(config)],
            cwd=tmp_path,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            deadline = time.monotonic() + 5
            while not any(b"/session " in request for request in requests):
                assert process.poll() is None
                assert time.monotonic() < deadline
                time.sleep(0.01)
            arguments = Path(f"/proc/{process.pid}/cmdline").read_bytes()
            environment = Path(f"/proc/{process.pid}/environ").read_bytes()
            process.send_signal(signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=5)
            assert process.returncode == 0
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)
        evidence = (
            first_bytes
            + (state / "state.json").read_bytes()
            + (result.stdout + result.stderr + stdout + stderr).encode()
        )
        assert SESSION.get_secret_value().encode() not in evidence
        for secret in (enrollment, TOKEN.get_secret_value(), SESSION.get_secret_value()):
            assert secret.encode() not in arguments + environment
            assert secret not in result.stdout + result.stderr + stdout + stderr
        assert not list(state.glob(".state-*"))
        assert not os.path.islink(state / "state.json")


def test_cli_does_not_claim_uninstalled_catalog(tmp_path):
    config = config_file(tmp_path)
    value = json.loads(config.read_bytes())
    value["observation"]["connectors"] = [
        dict(name="uninstalled", version="1.0.0", artifact_sha256="a" * 64)
    ]
    config.write_text(json.dumps(value))
    assert cli_module().main(["run", "--config", str(config)]) == 2


def test_cli_accepts_relative_private_input_without_following_links(tmp_path, monkeypatch):
    from lotuspond_runtime.errors import FleetError

    token = tmp_path / "token"
    token.write_text("lpe_" + "A" * 43)
    token.chmod(0o600)
    monkeypatch.chdir(tmp_path)
    assert cli_module()._token("token").get_secret_value() == "lpe_" + "A" * 43
    (tmp_path / "alias").symlink_to(token)
    with pytest.raises(FleetError):
        cli_module()._token("alias")


def test_activity_worker_command_refuses_without_installed_providers(tmp_path, capsys):
    module = cli_module()
    assert module.main(["activity-worker", "--config", str(config_file(tmp_path))]) == 3
    assert capsys.readouterr().err.strip() == "unavailable"


def activity_config(tmp_path, **changes):
    from fixtures.activity_provider import CONNECTOR, INSTALLED, MODEL

    path = tmp_path / "activity.json"
    data = {
        "trust_file": str(tmp_path / "trust.json"),
        "journal_directory": str(tmp_path / "journal"),
        "providers": [
            {
                "provider_id": "fixture-read",
                "dependency": CONNECTOR.model_dump(),
                "operations": ["read"],
                "connector": INSTALLED.model_dump(),
                "script": {"read": {"value": None}},
            },
            {"provider_id": "fixture-model", "dependency": MODEL.model_dump()},
        ],
        **changes,
    }
    path.write_text(json.dumps(data))
    path.chmod(0o600)
    return path


def test_activity_worker_runs_synthetic_providers_only_in_test_mode(tmp_path, capsys):
    """Task 13: only test mode runs the packaged synthetic providers. Other modes accept
    only an operator's real-provider configuration (`provider_config`), so a synthetic
    one is refused there as invalid, and no configuration at all is unavailable."""
    module = cli_module()
    activity = str(activity_config(tmp_path))
    for mode in ("production", "development"):
        config = str(config_file(tmp_path, mode=mode))
        assert (
            module.main(["activity-worker", "--config", config, "--activity-config", activity]) == 2
        )
        assert capsys.readouterr().err.strip() == "invalid_input"
        assert module.main(["activity-worker", "--config", config]) == 3
        assert capsys.readouterr().err.strip() == "unavailable"
    # Test mode needs its activity configuration, and it must be exact closed data.
    config = str(config_file(tmp_path, mode="test"))
    assert module.main(["activity-worker", "--config", config]) == 3
    capsys.readouterr()
    for bad in (
        {"trust_file": "relative.json"},
        {"providers": [{"provider_id": "real-model", "dependency": {}}]},
        {"providers": []},
        {"extra": True},
    ):
        invalid = str(activity_config(tmp_path, **bad))
        assert (
            module.main(["activity-worker", "--config", config, "--activity-config", invalid]) == 2
        )
        assert capsys.readouterr().err.strip() == "invalid_input"


def test_synthetic_worker_configuration_and_trust_are_reread(tmp_path):
    from test_state import private_dir

    from lotuspond_runtime.activity_fixture import FixtureTrust, worker_config

    directory = private_dir(tmp_path)
    trust_file, journal, descriptors = worker_config(activity_config(directory).read_bytes())
    assert (trust_file.name, journal.name) == ("trust.json", "journal")
    assert [d.provider_id for d in descriptors] == ["fixture-read", "fixture-model"]
    assert all(d.test_only for d in descriptors)
    keys = {"format_version": 1, "keys": []}
    path = directory / "trust.json"
    path.write_text(json.dumps(keys))
    path.chmod(0o600)
    trust = FixtureTrust(path)
    assert list(trust.current(None, 0).keys) == []
    public = bytes(range(32))
    entry = {"key_id": hashlib.sha256(public).hexdigest(), "public_key": public.hex()}
    rotated = {"format_version": 1, "keys": [{**entry, "state": "allowed"}]}
    path.write_text(json.dumps(rotated))
    assert len(trust.current(None, 0).keys) == 1  # a rewritten file is seen at once


def test_test_mode_reports_only_the_connectors_its_action_providers_serve(tmp_path, capsys):
    """E05: a test-mode runtime may report the synthetic connector its action providers
    serve; reporting one that none serves refuses before any transport is built."""
    pin = dict(name="synthetic-target", version="1.0.0", artifact_sha256="e" * 64)
    module = cli_module()
    config = config_file(tmp_path, mode="test")
    value = json.loads(config.read_bytes())
    value["observation"]["connectors"] = [pin]
    config.write_text(json.dumps(value))
    plain = str(activity_config(tmp_path))  # valid, but serves no action connector
    assert (
        module.main(["activity-worker", "--config", str(config), "--activity-config", plain]) == 2
    )
    assert capsys.readouterr().err.strip() == "invalid_input"
    # Positive control: an action provider serving it passes the guard, and the worker
    # proceeds until it finds it was never enrolled.
    serving = activity_config(
        tmp_path,
        actions={
            "trust_file": str(tmp_path / "command-trust.json"),
            "providers": [
                {
                    "provider_id": "synthetic-target-write",
                    "connector": pin,
                    "operations": {"submit": "target_key"},
                    "script": {"ca_file": str(tmp_path / "ca.pem")},
                }
            ],
        },
    )
    code = module.main(
        ["activity-worker", "--config", str(config), "--activity-config", str(serving)]
    )
    assert (code, capsys.readouterr().err.strip()) != (2, "invalid_input")
    # No other command reports one, even in test mode.
    assert module.main(["run", "--config", str(config)]) == 2
    # Production mode's other commands report none either.
    value["mode"] = "production"
    config.write_text(json.dumps(value))
    assert module.main(["run", "--config", str(config)]) == 2


def test_a_production_worker_reports_the_connectors_its_write_providers_serve(tmp_path, capsys):
    """E05 outside test mode: an operator's `https-json` write providers let the worker
    report their connector; one they do not serve is refused before any transport."""
    from test_https_json import write_routes
    from test_operator_actions import operator_config

    from lotuspond_runtime.https_json import routes_digest

    module = cli_module()
    served = dict(name="ap-standin", version="0.1.0", artifact_sha256=routes_digest(write_routes()))
    config = config_file(tmp_path, mode="production")
    value = json.loads(config.read_bytes())
    activity = tmp_path / "operator-activity.json"
    for raw, connector, refused in (
        (operator_config(tmp_path, actions=False), served, True),
        (operator_config(tmp_path, actions=True), {**served, "version": "9.9.9"}, True),
        (operator_config(tmp_path, actions=True), served, False),
    ):
        value["observation"]["connectors"] = [connector]
        config.write_text(json.dumps(value))
        activity.write_bytes(raw)
        activity.chmod(0o600)
        code = module.main(
            ["activity-worker", "--config", str(config), "--activity-config", str(activity)]
        )
        outcome = (code, capsys.readouterr().err.strip())
        assert (outcome == (2, "invalid_input")) is refused, outcome


def test_synthetic_target_read_provider_is_a_declared_read(tmp_path):
    """E05: the synthetic target's read provider serves read-backs in test mode."""
    from fixtures.activity_provider import CONNECTOR, INSTALLED

    from lotuspond_runtime.activity_fixture import worker_config

    path = activity_config(
        tmp_path,
        providers=[
            {
                "provider_id": "synthetic-target-read",
                "dependency": CONNECTOR.model_dump(),
                "operations": ["lookup"],
                "connector": INSTALLED.model_dump(),
                "script": {"ca_file": str(tmp_path / "ca.pem")},
            }
        ],
    )
    _, _, [descriptor] = worker_config(path.read_bytes())
    assert (descriptor.provider_id, descriptor.effect) == ("synthetic-target-read", "read")
    assert descriptor.operations == frozenset({"lookup"}) and descriptor.test_only
    unknown = activity_config(tmp_path, providers=[{"provider_id": "synthetic-target-write"}])
    with pytest.raises(ValueError):
        worker_config(unknown.read_bytes())
