"""Fresh, synthetic source documents shared with compiler tests."""

import copy
import json
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parents[4] / "contracts/examples/executable-v2"


@pytest.fixture
def case():
    def load(name: str) -> dict:
        return copy.deepcopy(json.loads((EXAMPLES / f"{name}.json").read_text()))

    return load


@pytest.fixture
def catalog_case(case):
    return lambda: case("catalog")


@pytest.fixture
def v1_example(example):
    return example
