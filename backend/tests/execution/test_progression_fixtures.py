"""Golden progression records for `lotuspond.execution-interpreter.v1`.

Each fixture holds canonical committed rows of one region occurrence, database
time and the expected next actions. Every fixture must re-derive byte-identically;
an incompatible interpreter change needs a new interpreter version and fixtures.
Recording is an explicit, reviewed action, never part of a test run:
`uv run --frozen python -m tests.execution.test_progression_fixtures` (from backend/).
"""

import json
from pathlib import Path

from lotuspond_sdk.executable import validate_executable_package

from lotuspond.execution.engine_contracts import INTERPRETER_V1
from lotuspond.execution.program import program_from
from lotuspond.execution.progression import derivation_json, derive, signal_digest, state_from_json

FLOW = Path(__file__).parent / "fixtures/control-flow"
GOLDEN = Path(__file__).parent / "fixtures/progression" / INTERPRETER_V1
CATALOG = Path(__file__).resolve().parents[3] / "contracts/examples/executable-v2/catalog.json"
EXECUTION = "00000000-0000-4000-8000-000000000001"
NOW = 1_800_000_000_000
DEADLINE = NOW + 86_400_000
REGIONS_INPUT = {"amount": "1.005", "items": [3, 1, 2], "flag": True}


def sources(names):
    found = []
    for name in names:
        if name.startswith("catalog:"):
            found.append(json.loads(CATALOG.read_text())["documents"][int(name.split(":")[1])])
        else:
            found.append(json.loads((FLOW / f"{name}.json").read_text()))
    return found


def program(case):
    package = validate_executable_package(
        json.loads((FLOW / f"{case['package']}.json").read_text())
    )
    return program_from(package, dict(enumerate(sources(case["sources"]))))


def ok(path, value, ordinal=1):
    return {
        "path": path,
        "attempts": [{"id": f"a-{path}", "ordinal": ordinal, "state": "succeeded", "value": value}],
    }


def failed(path, code, ordinal=1, at=NOW):
    return {
        "path": path,
        "attempts": [
            {
                "id": f"a-{path}",
                "ordinal": ordinal,
                "state": "failed",
                "failure": code,
                "failed_at_ms": at,
            }
        ],
    }


def wait(path, state, deadline, signals=()):
    return {
        "path": path,
        "attempts": [],
        "wait": {"id": f"w-{path}", "state": state, "deadline_ms": deadline},
        "signals": list(signals),
    }


def signal(id, wait_path, payload, at, expires=NOW + 1000, digest_wait=None):
    return {
        "id": id,
        "signal_id": id,
        "digest": signal_digest(EXECUTION, id, digest_wait or f"w-{wait_path}", payload),
        "available_at_ms": at,
        "expires_at_ms": expires,
        "payload": payload,
    }


def case(name, package, occurrences=(), region="", input=None, now=NOW, sources=(), **extra):
    state = {
        "execution_id": EXECUTION,
        "region_path": region,
        "requested_state": extra.pop("requested_state", "running"),
        "deadline_ms": extra.pop("deadline_ms", DEADLINE),
        "input": input,
        "occurrences": list(occurrences),
        **extra,
    }
    return {
        "name": name,
        "interpreter": INTERPRETER_V1,
        "package": package,
        "sources": list(sources),
        "now_ms": now,
        "state": state,
    }


HEAD = [ok("total", "1.00"), ok("choose#select", {"branch": "then"}), ok("choose", "high")]
ITEMS = HEAD + [ok("each#items", [3, 1, 2])]
DONE = ITEMS + [ok("each", [6, 2, 4]), ok("both", {"left": "L", "right": "R"})]
POLICY = ["waits.policy"]
MODELS = ["catalog:0", "catalog:1"]
CASES = [
    case("regions-transform-decimal", "regions", input=REGIONS_INPUT),
    case("regions-decision-select", "regions", [ok("total", "1.00")], input=REGIONS_INPUT),
    case("regions-decision-branch", "regions", region="choose:then", input=REGIONS_INPUT),
    case(
        "regions-decision-complete",
        "regions",
        [ok("choose:then/high", "high")],
        region="choose:then",
        input=REGIONS_INPUT,
    ),
    case("regions-iteration-pin", "regions", HEAD, input=REGIONS_INPUT),
    case(
        "regions-iteration-item", "regions", region="each:1", input=REGIONS_INPUT, item=1, index=1
    ),
    case(
        "regions-iteration-item-complete",
        "regions",
        [ok("each:1/double", 2)],
        region="each:1",
        input=REGIONS_INPUT,
        item=1,
        index=1,
    ),
    case(
        "regions-iteration-ordered",
        "regions",
        ITEMS + [ok("each:2", 4), ok("each:1", 2), ok("each:0", 6)],
        input=REGIONS_INPUT,
    ),
    case(
        "regions-parallel-join",
        "regions",
        ITEMS + [ok("each", [6, 2, 4]), ok("both:left", "L"), ok("both:right", "R")],
        input=REGIONS_INPUT,
    ),
    case(
        "regions-parallel-failure",
        "regions",
        ITEMS
        + [ok("each", [6, 2, 4]), ok("both:left", "L"), failed("both:right", "type_mismatch")],
        input=REGIONS_INPUT,
    ),
    case("regions-coalesce-missing", "regions", DONE, input=REGIONS_INPUT),
    case("regions-coalesce-present", "regions", DONE, input={**REGIONS_INPUT, "note": "given"}),
    case("regions-complete", "regions", DONE + [ok("fallback", "none")], input=REGIONS_INPUT),
    case("regions-deadline", "regions", HEAD, input=REGIONS_INPUT, deadline_ms=NOW),
    case("regions-paused", "regions", HEAD, input=REGIONS_INPUT, requested_state="paused"),
    case("waits-timer-open", "waits", sources=POLICY),
    case(
        "waits-timer-pending",
        "waits",
        [wait("pause", "open", NOW + 60_000)],
        sources=POLICY,
        now=NOW + 30_000,
    ),
    case(
        "waits-timer-satisfied", "waits", [wait("pause", "satisfied", NOW + 60_000)], sources=POLICY
    ),
    case(
        "waits-signal-consume",
        "waits",
        [
            ok("pause", None),
            wait(
                "callback",
                "open",
                NOW + 5_000,
                [
                    signal("s1", "callback", 7, NOW - 50, digest_wait="w-other"),
                    signal("s2", "callback", "seven", NOW - 40),
                    signal("s3", "callback", 7, NOW - 30, expires=NOW - 40),
                    signal("s4", "callback", 9, NOW - 20),
                    signal("s5", "callback", 10, NOW - 10),
                ],
            ),
        ],
        sources=POLICY,
    ),
    case(
        "waits-signal-expired",
        "waits",
        [ok("pause", None), wait("callback", "expired", NOW)],
        sources=POLICY,
    ),
    case("waits-form-open", "waits", [ok("pause", None), ok("callback", 9)], sources=POLICY),
    case("activities-create", "activities", sources=MODELS),
    case(
        "activities-retry-wait",
        "activities",
        [failed("read", "unavailable", at=NOW - 1_000)],
        sources=MODELS,
    ),
    case(
        "activities-retry",
        "activities",
        [failed("read", "unavailable", at=NOW - 5_000)],
        sources=MODELS,
    ),
    case(
        "activities-exhausted",
        "activities",
        [failed("read", "unavailable", ordinal=3)],
        sources=MODELS,
    ),
    case("activities-next", "activities", [ok("read", None)], sources=MODELS),
    case("subworkflow-pin", "subworkflow", input=5, sources=["subworkflow.child"]),
    case(
        "subworkflow-call",
        "subworkflow",
        [ok("invoke#input", 5)],
        input=5,
        sources=["subworkflow.child"],
    ),
    case(
        "subworkflow-child-step",
        "subworkflow",
        region="invoke:call",
        input=5,
        sources=["subworkflow.child"],
    ),
    case(
        "subworkflow-child-complete",
        "subworkflow",
        [ok("invoke:call/triple", 15)],
        region="invoke:call",
        input=5,
        sources=["subworkflow.child"],
    ),
    case(
        "subworkflow-complete",
        "subworkflow",
        [ok("invoke#input", 5), ok("invoke", 15)],
        input=5,
        sources=["subworkflow.child"],
    ),
    case(
        "waits-form-consume",
        "waits",
        [
            ok("pause", None),
            ok("callback", 9),
            wait(
                "review",
                "open",
                NOW + 5_000,
                [
                    signal("f1", "review", {"approved": "yes"}, NOW - 20),
                    signal("f2", "review", {"approved": True}, NOW - 10),
                ],
            ),
        ],
        sources=POLICY,
    ),
    case(
        "decimal-item-below",
        "decimal_items",
        region="each:0",
        input=["9.50", "10.50"],
        item="9.50",
        index=0,
        outer={"limit": "10.00"},
    ),
    case(
        "decimal-item-above",
        "decimal_items",
        region="each:1",
        input=["9.50", "10.50"],
        item="10.50",
        index=1,
        outer={"limit": "10.00"},
    ),
    case(
        "scoped-outer-reference",
        "scoped",
        region="each:1/pick:then",
        input=[1, 2, 3],
        item=2,
        index=1,
        outer={"base": 10},
    ),
]


def derive_bytes(record):
    plan = program(record)
    state = state_from_json(plan, record["state"])
    return derivation_json(derive(plan, state, record["now_ms"]))


def test_golden_progression_fixtures_rederive():
    files = sorted(GOLDEN.glob("*.json"))
    assert {path.stem for path in files} == {record["name"] for record in CASES}
    kinds = set()
    for path in files:
        record = json.loads(path.read_text())
        assert record["interpreter"] == INTERPRETER_V1
        expected = actions_json_from(record["derivation"])
        assert derive_bytes(record) == expected, path.name
        kinds |= {action["type"] for action in record["derivation"]["actions"]}
    # Every action kind except a signal-free consumption appears in some fixture.
    assert kinds >= {
        "RecordPure",
        "CreateAttempt",
        "OpenWait",
        "ConsumeSignal",
        "CancelUndispatched",
        "Complete",
        "Fail",
    }


def actions_json_from(items):
    from lotuspond.execution.contracts import canonical_value

    return canonical_value(items)


def record():
    GOLDEN.mkdir(parents=True, exist_ok=True)
    for item in CASES:
        content = {**item, "derivation": json.loads(derive_bytes(item))}
        (GOLDEN / f"{item['name']}.json").write_text(
            json.dumps(content, indent=1, sort_keys=True) + "\n"
        )


if __name__ == "__main__":
    record()
