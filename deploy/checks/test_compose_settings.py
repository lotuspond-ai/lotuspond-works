"""Exercise operator settings through real Compose interpolation; no containers run."""

import base64
import json
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from lotuspond.config import Settings
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(params=["compose.yaml", "compose.onprem.yaml"])
def rendered_services(request, tmp_path):
    # All values are synthetic; do not inherit a developer's deployment configuration.
    variables = {
        "LOTUSPOND_API_IMAGE": "lotuspond-api:config-test",
        "LOTUSPOND_COLLECTOR_IMAGE": "lotuspond-collector:config-test",
        "POSTGRES_IMAGE": "postgres:config-test",
        "REDIS_IMAGE": "redis:config-test",
        "POSTGRES_PASSWORD": "synthetic-database-password",
        "REDIS_PASSWORD": "synthetic-redis-password",
        "LOTUSPOND_DATABASE_URL": "postgresql+psycopg://test:synthetic@postgres/lotuspond",
        "LOTUSPOND_REDIS_URL": "redis://:synthetic@redis:6379/0",
        "LOTUSPOND_ORIGIN": "https://lotuspond.example.com",
        "COLLECTOR_INGEST_TOKEN": "synthetic-collector-token-for-config-test",
        "LOTUSPOND_API_KEY": "synthetic-api-key-for-config-test",
        "LOTUSPOND_PROJECT_ID": "11111111-1111-4111-8111-111111111111",
        "LOTUSPOND_REQUIRE_MFA": "true",
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


def test_compose_applies_installation_mfa_and_fails_closed_without_key(
    rendered_services,
):
    api = rendered_services["api"]
    assert service_settings(api).require_mfa is True
    api["environment"]["LOTUSPOND_MFA_ENCRYPTION_KEY"] = ""
    with pytest.raises(ValidationError, match="authenticator requirement needs"):
        service_settings(api)
    for name in ("worker", "mail-worker"):
        assert not rendered_services[name]["environment"].get(
            "LOTUSPOND_MFA_ENCRYPTION_KEY"
        )


def test_compose_applies_whole_smtp_bound_to_api_and_mail_worker(rendered_services):
    for name in ("api", "mail-worker"):
        settings = service_settings(rendered_services[name])
        assert settings.smtp_timeout_seconds == 20
        assert settings.smtp_conversation_seconds == 90
