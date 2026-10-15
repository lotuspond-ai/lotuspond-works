"""Build independent artifacts and install without editable/source overrides."""

import hashlib
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

RUNTIME = Path(__file__).resolve().parents[1]
REPO = RUNTIME.parents[1]
SDK = REPO / "sdk/python"


def artifact_run(args, cwd, env):
    return subprocess.run(
        args, cwd=cwd, env=env, check=True, capture_output=True, text=True, timeout=180
    )


@pytest.fixture(scope="session")
def installed_artifacts(tmp_path_factory):
    root = tmp_path_factory.mktemp("installed-runtime")
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
    direct, sdk, rebuilt = [root / name for name in ("direct", "sdk", "rebuilt")]
    artifact_run(
        ["uv", "build", "--wheel", "--project", str(SDK), "--out-dir", str(sdk)], root, env
    )
    artifact_run(
        ["uv", "build", "--wheel", "--project", str(RUNTIME), "--out-dir", str(direct)], root, env
    )
    artifact_run(
        ["uv", "build", "--sdist", "--project", str(RUNTIME), "--out-dir", str(direct)], root, env
    )
    source = root / "source"
    with tarfile.open(next(direct.glob("*.tar.gz"))) as archive:
        archive.extractall(source, filter="data")
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
        "interpreter": sys.version,
        "source": {
            str(p.relative_to(RUNTIME)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((RUNTIME / "src").rglob("*"))
            if p.is_file() and "__pycache__" not in p.parts
        },
        "artifacts": {
            str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for directory in (direct, sdk, rebuilt)
            for p in sorted(directory.iterdir())
            if p.is_file()
        },
    }
    (root / "artifact-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Artifact manifest: {root / 'artifact-manifest.json'}")
    installed = []
    for index, wheel in enumerate((next(direct.glob("*.whl")), next(rebuilt.glob("*.whl")))):
        venv = root / f"venv-{index}"
        artifact_run(["uv", "venv", "--python", sys.executable, str(venv)], root, env)
        artifact_run(
            [
                "uv",
                "pip",
                "install",
                "--python",
                str(venv / "bin/python"),
                str(next(sdk.glob("*.whl"))),
                str(wheel),
            ],
            root,
            env,
        )
        artifact_run(["uv", "pip", "check", "--python", str(venv / "bin/python")], root, env)
        installed.append((venv, env))
    return installed


@pytest.fixture(params=(0, 1), ids=("wheel", "sdist-wheel"))
def installed_runtime(request, installed_artifacts):
    return installed_artifacts[request.param]
