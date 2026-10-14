"""Exact installed dependency metadata, package origin and pure import boundary."""

from conftest import artifact_run


def test_installed_metadata(installed_runtime, tmp_path):
    venv, env = installed_runtime
    artifact_run(
        [
            str(venv / "bin/python"),
            "-I",
            "-c",
            """
import importlib.metadata as m, importlib.util, sys
from pathlib import Path
import lotuspond_runtime, lotuspond_runtime.protocol, lotuspond_sdk
assert m.version('lotuspond-runtime') == '0.1.0'
assert m.version('lotuspond-sdk') == '0.2.0'
assert set(m.requires('lotuspond-runtime')) == {'lotuspond-sdk==0.2.0', 'pydantic<3,>=2.12', 'rfc8785==0.1.4'}
assert set(m.requires('lotuspond-sdk')) == {'cryptography<51,>=50', 'pydantic<3,>=2.12', 'rfc8785==0.1.4'}
for module in (lotuspond_runtime, lotuspond_sdk):
 assert Path(module.__file__).is_relative_to(Path(sys.prefix))
 assert Path(module.__file__).with_name('py.typed').is_file()
for name in ('fastapi', 'sqlalchemy', 'psycopg', 'redis', 'httpx', 'lotuspond'):
 assert importlib.util.find_spec(name) is None
 assert name not in sys.modules
assert 'lotuspond_runtime.client' not in sys.modules
from lotuspond_runtime.protocol import decode_fleet_json, connection_digest
assert connection_digest(decode_fleet_json(b'{"connector":{"name":"lotuspond-fixture","version":"1.0.0","artifact_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},"operations":["inspect"],"destinations":[],"secrets":[],"data_handling":{},"format_version":1}')) == 'b986684ecd1f4fca794d3ad8dfc808ebabcc311f256992de86dabc6e0cb768e8'
""",
        ],
        tmp_path,
        env,
    )
