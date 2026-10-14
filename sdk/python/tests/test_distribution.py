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
for forbidden in ('fastapi', 'sqlalchemy', 'psycopg', 'redis', 'httpx', 'lotuspond'):
    assert importlib.util.find_spec(forbidden) is None, forbidden
assert not any(name.split('.')[0] in ('fastapi', 'sqlalchemy', 'psycopg', 'redis', 'httpx', 'lotuspond') for name in sys.modules)
assert importlib.metadata.version('lotuspond-sdk') == '0.2.0'
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
