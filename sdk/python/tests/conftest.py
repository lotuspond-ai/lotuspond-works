import hashlib
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
SDK = Path(__file__).resolve().parents[1]


@pytest.fixture
def golden_bytes():
    return (FIXTURES / "reviewed-action.canonical.json").read_bytes()


@pytest.fixture
def example(golden_bytes):
    return json.loads(golden_bytes)


def artifact_run(args, cwd, env):
    return subprocess.run(
        args, cwd=cwd, env=env, check=True, capture_output=True, text=True, timeout=180
    )


@pytest.fixture(scope="session")
def installed_artifacts(tmp_path_factory):
    root = tmp_path_factory.mktemp("installed-sdk")
    env = {
        key: value
        for key, value in os.environ.items()
        if key
        in (
            "PATH",
            "HOME",
            "TMPDIR",
            "UV_CACHE_DIR",
            "UV_PYTHON_INSTALL_DIR",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
            "UV_NATIVE_TLS",
            "UV_SYSTEM_CERTS",
            "HTTPS_PROXY",
            "HTTP_PROXY",
            "NO_PROXY",
        )
    }
    artifacts = root / "artifacts"
    # uv build without selectors builds its wheel from the sdist. Explicit
    # selectors retain genuinely independent direct-wheel and sdist-wheel origins.
    artifact_run(
        ["uv", "build", "--wheel", "--project", str(SDK), "--out-dir", str(artifacts)], root, env
    )
    artifact_run(
        ["uv", "build", "--sdist", "--project", str(SDK), "--out-dir", str(artifacts)], root, env
    )
    source = root / "source"
    with tarfile.open(next(artifacts.glob("*.tar.gz"))) as archive:
        archive.extractall(source, filter="data")
    rebuilt = root / "rebuilt"
    artifact_run(
        [
            "uv",
            "build",
            "--wheel",
            "--project",
            str(next(source.iterdir())),
            "--out-dir",
            str(rebuilt),
        ],
        root,
        env,
    )
    manifest = {
        "source": {
            str(path.relative_to(SDK)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((SDK / "src").rglob("*"))
            if path.is_file() and "__pycache__" not in path.parts
        },
        "artifacts": {
            str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for directory in (artifacts, rebuilt)
            for path in sorted(directory.iterdir())
            if path.is_file()
        },
        "interpreter": sys.version,
    }
    (root / "artifact-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    installed = []
    for index, wheel in enumerate((next(artifacts.glob("*.whl")), next(rebuilt.glob("*.whl")))):
        venv = root / f"venv-{index}"
        artifact_run(["uv", "venv", "--python", sys.executable, str(venv)], root, env)
        artifact_run(
            [
                "uv",
                "pip",
                "install",
                "--python",
                str(venv / "bin/python"),
                str(wheel),
            ],
            root,
            env,
        )
        installed.append((venv, env))
    return installed


@pytest.fixture(params=(0, 1), ids=("wheel", "sdist-wheel"))
def installed_sdk(request, installed_artifacts):
    return installed_artifacts[request.param]


@pytest.fixture
def invoke(installed_sdk, tmp_path):
    venv, env = installed_sdk

    def command(*args, module=False):
        executable = (
            [str(venv / "bin/python"), "-I", "-m", "lotuspond_sdk"]
            if module
            else [str(venv / "bin/lotuspond-package")]
        )
        return subprocess.run(
            [*executable, *map(str, args)],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )

    return command
