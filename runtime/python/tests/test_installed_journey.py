"""Installed consumers verify exact records without editable/source imports."""

from pathlib import Path

from conftest import artifact_run


def test_installed_no_effect_journey(installed_runtime, tmp_path):
    venv, env = installed_runtime
    setup = Path(__file__).with_name("command_fixtures.py").read_text()
    script = (
        setup
        + """
import importlib.metadata as metadata
import json
import sys
from pathlib import Path
import lotuspond_runtime
import lotuspond_sdk
from lotuspond_runtime.commands import validate_delivery
assert metadata.version("lotuspond-runtime") == "0.1.0"
assert metadata.version("lotuspond-sdk") == "0.3.0"
for name, module in (("lotuspond-runtime", lotuspond_runtime), ("lotuspond-sdk", lotuspond_sdk)):
 assert Path(module.__file__).is_relative_to(Path(sys.prefix))
 direct = metadata.distribution(name).read_text("direct_url.json")
 assert not direct or not json.loads(direct).get("dir_info", {}).get("editable", False)
case = command_case()
assert validate_delivery(delivery(case), identity=case.identity, records=records(case), trust=snapshot(case), now_ms=case.now) == case.command
print("installed_no_effect_journey_passed")
"""
    )
    result = artifact_run([str(venv / "bin/python"), "-I", "-c", script], tmp_path, env)
    assert result.stdout.strip() == "installed_no_effect_journey_passed"
