"""Separate structural schema parity and kind/dependency/operation correlation."""

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from lotuspond_sdk.activity import activity_schema, validate_activity
from lotuspond_sdk.executable import ExecutableError


def test_separate_committed_schema_parity():
    schema = activity_schema()
    Draft202012Validator.check_schema(schema)
    target = Path(__file__).resolve().parents[4] / "contracts/runtime-activity.v1.schema.json"
    rendered = (json.dumps(schema, sort_keys=True, indent=2, ensure_ascii=True) + "\n").encode()
    assert target.read_bytes() == rendered
    raw = json.loads(Path(__file__).with_name("fixtures").joinpath("read.json").read_bytes())
    validator = Draft202012Validator(schema)
    validator.validate(raw)
    model = copy.deepcopy(raw)
    model.update(kind="model", operation=None)
    model["dependency"]["kind"] = "model"
    validator.validate(model)
    validate_activity(model)
    for update in ({"operation": "inspect"}, {"kind": "connector_read"},
                   {"workspace_id": "opaque"}, {"generation": 0}, {"issued_at_ms": True},
                   {"extra": 1}):
        bad = {**model, **update}
        assert not validator.is_valid(bad)
        with pytest.raises(ExecutableError):
            validate_activity(bad)
    for name in raw:
        bad = dict(raw)
        del bad[name]
        assert not validator.is_valid(bad), name


def test_schema_is_fresh_and_structural_only():
    schema = activity_schema()
    schema["properties"].clear()
    assert activity_schema()["properties"]
    raw = json.loads(Path(__file__).with_name("fixtures").joinpath("read.json").read_bytes())
    raw["expires_at_ms"] += 1
    assert Draft202012Validator(activity_schema()).is_valid(raw)
    with pytest.raises(ExecutableError):
        validate_activity(raw)
