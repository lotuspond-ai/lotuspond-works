import json
from pathlib import Path

import lotuspond_sdk
from lotuspond_sdk import cli
from lotuspond_sdk import input as sdk_input

from lotuspond import package_contract as legacy
from lotuspond import package_input as legacy_input
from lotuspond.package_cli import main, read_package


def test_legacy_modules_share_the_contract():
    assert legacy.WorkflowPackage is lotuspond_sdk.WorkflowPackage
    assert legacy_input.PackageReadError is sdk_input.PackageReadError
    assert legacy.validate_package is lotuspond_sdk.validate_package
    assert legacy.canonical_package_json is lotuspond_sdk.canonical_package_json
    assert legacy.VALIDATOR_REVISION == lotuspond_sdk.VALIDATOR_REVISION
    assert legacy.CANONICALIZATION == lotuspond_sdk.CANONICALIZATION


def test_legacy_cli_retains_decoded_object_and_output(capsys):
    path = Path(__file__).resolve().parents[2] / "contracts/examples/reviewed-action.v1.json"
    assert type(read_package(path)) is dict
    assert main(["validate", str(path)]) == 0
    old = capsys.readouterr()
    assert cli.main(["validate", str(path)]) == 0
    assert capsys.readouterr() == old
    assert (
        json.loads(old.out)["sha256"]
        == "305efc96011ba5fc2f527cefbe416890b0265269e18e3402354ffbfed40251d3"
    )
