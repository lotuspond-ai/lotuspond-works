import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def golden_bytes():
    return (FIXTURES / "reviewed-action.canonical.json").read_bytes()


@pytest.fixture
def example(golden_bytes):
    return json.loads(golden_bytes)
