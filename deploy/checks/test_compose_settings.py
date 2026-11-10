"""Exercise operator settings through real Compose interpolation; no containers run."""

import base64
import json
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from lotuspond.config import Settings

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(params=["compose.yaml"])
def rendered_services(request, tmp_path):
    # All values are synthetic; do not inherit a developer's deployment configuration.
    variables = {
        "LOTUSPOND_API_IMAGE": "lotuspond-api:config-test",
        "POSTGRES_IMAGE": "postgres:config-test",
        "REDIS_IMAGE": "redis:config-test",
        "POSTGRES_PASSWORD": "synthetic-database-password",
        "REDIS_PASSWORD": "synthetic-redis-password",
        "LOTUSPOND_DATABASE_URL": "postgresql+psycopg://test:synthetic@postgres/lotuspond",
        "LOTUSPOND_REDIS_URL": "redis://:synthetic@redis:6379/0",
        "LOTUSPOND_ORIGIN": "https://lotuspond.example.com",
        "LOTUSPOND_MFA_ENCRYPTION_KEY": base64.urlsafe_b64encode(bytes(32)).decode(),
        "LOTUSPOND_SMTP_TIMEOUT_SECONDS": "20",
        "LOTUSPOND_SMTP_CONVERSATION_SECONDS": "90",
    }
    env_file = tmp_path / "synthetic.env"
    env_file.write_text("".join(f"{key}={value}\n" for key, value in variables.items()))
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(env_file),
            "--project-directory",
            str(tmp_path),
            "-f",
            str(ROOT / "deploy" / request.param),
            "--profile",
            "mail",
            "config",
            "--format",
            "json",
        ],
        env={"PATH": os.environ["PATH"], "DOCKER_CONFIG": str(tmp_path / "docker")},
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return json.loads(result.stdout)["services"]


def service_settings(service):
    with patch.dict(os.environ, service["environment"], clear=True):
        return Settings(_env_file=None)


def test_compose_gives_the_mfa_key_to_the_api_only(rendered_services):
    api = rendered_services["api"]
    assert service_settings(api).mfa_encryption_key is not None
    # The installation-wide authenticator requirement was retired (ADR-0014).
    assert "LOTUSPOND_REQUIRE_MFA" not in api["environment"]
    assert not rendered_services["mail-worker"]["environment"].get(
        "LOTUSPOND_MFA_ENCRYPTION_KEY"
    )


def test_compose_runs_no_retired_assurance_worker(rendered_services):
    assert set(rendered_services) == {"api", "mail-worker", "postgres", "redis"}
    for service in rendered_services.values():
        assert not any(key.startswith("LOTUSPOND_ALERT_") for key in service.get("environment", {}))


def test_compose_applies_whole_smtp_bound_to_api_and_mail_worker(rendered_services):
    for name in ("api", "mail-worker"):
        settings = service_settings(rendered_services[name])
        assert settings.smtp_timeout_seconds == 20
        assert settings.smtp_conversation_seconds == 90
