"""ADR-0013 step 3: the Studio's five domain patterns as executable-v2 packages.

Each pattern publishes its synthetic catalog documents into an environment and then passes
the server's own check (validator, compiler, closure and E05's action rules), exactly as the
Studio would ask before signing. The generator's output is also current.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from .test_package_check import check
from .test_publication import harness as harness  # noqa: F401

PATTERNS = Path(__file__).resolve().parents[3] / "contracts/examples/executable-v2/patterns"
NAMES = [
    "reviewed-action",
    "document-intake",
    "case-approval",
    "knowledge-answer",
    "record-reconciliation",
]


def test_the_generated_patterns_are_current():
    result = subprocess.run(
        [sys.executable, str(PATTERNS / "generate.py"), "--check"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("name", NAMES)
def test_each_pattern_passes_the_servers_check_with_its_catalog(harness, name):
    h = harness
    package = json.loads((PATTERNS / f"{name}.json").read_text())
    documents = json.loads((PATTERNS / f"{name}.catalog.json").read_text())["documents"]
    ids = []
    with Session(h.engine) as db, db.begin():
        for n, document in enumerate(documents):
            ids.append(h.publish(db, document, request_key=f"{name}-{n}").id)
    answer = check(h, package, ids)
    assert answer["valid"] is True, answer["problems"]
    assert answer["package_sha256"] and answer["plan_sha256"]
    # Every write is a reviewed action with a read-back verification.
    kinds = [step["kind"] for step in package["body"]["steps"]]
    assert kinds.count("action_dispatch") == kinds.count("outcome_verification") >= 1
