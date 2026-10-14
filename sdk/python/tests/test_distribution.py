"""Build and install wheel and sdist-derived wheel in isolated environments."""

import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

SDK = Path(__file__).resolve().parents[1]


def run(args, cwd, env):
    return subprocess.run(
        args, cwd=cwd, env=env, check=True, capture_output=True, text=True, timeout=180
    )


def test_wheel_and_sdist_are_standalone(tmp_path, golden_bytes):
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("VIRTUAL_ENV", None)
    env.pop("UV_PROJECT_ENVIRONMENT", None)
    artifacts = tmp_path / "artifacts"
    run(["uv", "build", "--project", str(SDK), "--out-dir", str(artifacts)], tmp_path, env)
    sdist = next(artifacts.glob("*.tar.gz"))
    source = tmp_path / "source"
    with tarfile.open(sdist) as archive:
        archive.extractall(source, filter="data")
    rebuilt = tmp_path / "rebuilt"
    run(
        [
            "uv",
            "build",
            "--wheel",
            "--project",
            str(next(source.iterdir())),
            "--out-dir",
            str(rebuilt),
        ],
        tmp_path,
        env,
    )
    path = tmp_path / "workflow.json"
    path.write_bytes(golden_bytes)
    for index, wheel in enumerate((next(artifacts.glob("*.whl")), next(rebuilt.glob("*.whl")))):
        venv = tmp_path / f"venv-{index}"
        run(["uv", "venv", "--python", sys.executable, str(venv)], tmp_path, env)
        python = venv / "bin/python"
        run(["uv", "pip", "install", "--python", str(python), str(wheel)], tmp_path, env)
        probe = run(
            [
                str(python),
                "-I",
                "-c",
                """
import importlib.metadata, importlib.util, json, sys
from pathlib import Path
import lotuspond_sdk
assert Path(lotuspond_sdk.__file__).is_relative_to(Path(sys.prefix))
assert Path(lotuspond_sdk.__file__).with_name('py.typed').is_file()
for forbidden in ('fastapi', 'sqlalchemy', 'psycopg', 'redis', 'httpx', 'lotuspond'):
    assert importlib.util.find_spec(forbidden) is None, forbidden
assert not any(name.split('.')[0] in ('fastapi', 'sqlalchemy', 'psycopg', 'redis', 'httpx', 'lotuspond') for name in sys.modules)
assert importlib.metadata.version('lotuspond-sdk') == '0.1.0'
print(json.dumps({'module': lotuspond_sdk.__file__}))
""",
            ],
            tmp_path,
            env,
        )
        assert str(venv) in json.loads(probe.stdout)["module"]
        for executable in (
            [str(python), "-I", "-m", "lotuspond_sdk"],
            [str(venv / "bin/lotuspond-package")],
        ):
            result = run([*executable, "validate", str(path)], tmp_path, env)
            assert result.stderr == ""
            assert json.loads(result.stdout) == {
                "ok": True,
                "schema_version": "lotuspond.workflow-package.v1",
                "sha256": "305efc96011ba5fc2f527cefbe416890b0265269e18e3402354ffbfed40251d3",
            }
