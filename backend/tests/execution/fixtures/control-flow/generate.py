"""Writes the synthetic control-flow packages and checks each with the SDK compiler.

Run from backend/: `uv run --frozen python tests/execution/fixtures/control-flow/generate.py`.
"""

import copy
import json
from hashlib import sha256
from pathlib import Path

from lotuspond_sdk.executable import canonical_executable_json, compile_package
from lotuspond_sdk.executable.catalog import catalog_document_digest

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[4]
EX = ROOT / "contracts/examples/executable-v2"
minimal = json.loads((EX / "minimal.json").read_text())
catalog = json.loads((EX / "catalog.json").read_text())["documents"]


def lit(value, schema):
    return {"kind": "literal", "schema": schema, "value": value}


def ref(pointer="", source=None):
    return {"kind": "ref", "source": source or {"kind": "package_input"}, "pointer": pointer}


def step_ref(step, pointer=""):
    return ref(pointer, {"kind": "step", "id": step})


def data(schema):
    return {"kind": "data", "schema": schema}


STR4 = {"kind": "string", "max_length": 4}
INT = {"kind": "integer"}
DEC = {"kind": "decimal", "precision": 38, "scale": 2}
NULL = {"kind": "null"}


def package(pid, input_schema, output_schema, steps, result, result_schema, **extra):
    p = copy.deepcopy(minimal)
    p.update(
        package_id=pid,
        name="Synthetic " + pid,
        input_schema=input_schema,
        output_schema=output_schema,
    )
    p["body"] = {"steps": steps, "result": result, "result_schema": result_schema}
    p.update(extra)
    return p


def ref_of(doc):
    return {
        "kind": doc["kind"],
        "name": doc["name"],
        "version": doc["version"],
        "sha256": catalog_document_digest(doc),
    }


def transform(sid, mapping, schema):
    return {"id": sid, "kind": "transform", "mapping": mapping, "output_schema": schema}


def body(steps, result, schema):
    return {"steps": steps, "result": result, "result_schema": schema}


regions_input = {
    "kind": "object",
    "fields": [
        {
            "name": "amount",
            "schema": {"kind": "decimal", "precision": 10, "scale": 3},
            "required": True,
        },
        {
            "name": "items",
            "schema": {
                "kind": "array",
                "items": {"kind": "integer", "min": 0, "max": 1000},
                "max_items": 5,
            },
            "required": True,
        },
        {"name": "flag", "schema": {"kind": "boolean"}, "required": True},
        {"name": "note", "schema": {"kind": "string", "max_length": 20}, "required": False},
    ],
}
regions_output = {
    "kind": "object",
    "fields": [
        {"name": "total", "schema": DEC, "required": True},
        {"name": "choose", "schema": STR4, "required": True},
        {
            "name": "each",
            "schema": {"kind": "array", "items": INT, "max_items": 5},
            "required": True,
        },
        {
            "name": "both",
            "schema": {
                "kind": "object",
                "fields": [
                    {"name": "left", "schema": STR4, "required": True},
                    {"name": "right", "schema": STR4, "required": True},
                ],
            },
            "required": True,
        },
        {"name": "note", "schema": {"kind": "string", "max_length": 20}, "required": True},
    ],
}
regions = package(
    "fixture-regions",
    regions_input,
    regions_output,
    [
        transform(
            "total",
            {
                "kind": "call",
                "fn": "add",
                "args": [
                    ref("/amount"),
                    lit("0.000", {"kind": "decimal", "precision": 10, "scale": 3}),
                ],
                "scale": 2,
                "rounding": "half_even",
            },
            DEC,
        ),
        {
            "id": "choose",
            "kind": "decision",
            "condition": ref("/flag"),
            "then_body": body(
                [transform("high", lit("high", STR4), STR4)], step_ref("high"), data(STR4)
            ),
            "else_body": body(
                [transform("low", lit("low", STR4), STR4)], step_ref("low"), data(STR4)
            ),
        },
        {
            "id": "each",
            "kind": "iteration",
            "input": ref("/items"),
            "max_items": 5,
            "concurrency": 2,
            "body": body(
                [
                    transform(
                        "double",
                        {
                            "kind": "call",
                            "fn": "multiply",
                            "args": [ref("", {"kind": "iteration_item"}), lit(2, INT)],
                        },
                        INT,
                    )
                ],
                step_ref("double"),
                data(INT),
            ),
        },
        {
            "id": "both",
            "kind": "parallel",
            "join": "all",
            "branches": [
                {
                    "id": "left",
                    "body": body(
                        [transform("lval", lit("L", STR4), STR4)], step_ref("lval"), data(STR4)
                    ),
                },
                {
                    "id": "right",
                    "body": body(
                        [transform("rval", lit("R", STR4), STR4)], step_ref("rval"), data(STR4)
                    ),
                },
            ],
        },
        transform(
            "fallback",
            {
                "kind": "call",
                "fn": "coalesce",
                "args": [ref("/note"), lit("none", {"kind": "string", "max_length": 20})],
            },
            {"kind": "string", "max_length": 20},
        ),
    ],
    {
        "kind": "object",
        "fields": [
            {"name": "total", "value": step_ref("total")},
            {"name": "choose", "value": step_ref("choose")},
            {"name": "each", "value": step_ref("each")},
            {"name": "both", "value": step_ref("both")},
            {"name": "note", "value": step_ref("fallback")},
        ],
    },
    data(regions_output),
)
regions["limits"]["max_steps"] = 100
plan = compile_package(regions, dependencies=[], catalog={"documents": []})
(OUT / "regions.json").write_text(json.dumps(regions, indent=1, sort_keys=True) + "\n")
print("regions compiled", len(plan.steps))

policy = copy.deepcopy(catalog[4])
policy["definition"]["assignment"] = "workspace_admins"
FORM = {
    "kind": "object",
    "fields": [{"name": "approved", "schema": {"kind": "boolean"}, "required": True}],
}
waits = package(
    "fixture-waits",
    NULL,
    {
        "kind": "object",
        "fields": [
            {"name": "signal", "schema": INT, "required": True},
            {"name": "form", "schema": FORM, "required": True},
        ],
    },
    [
        {"id": "pause", "kind": "timer", "delay_seconds": 60},
        {
            "id": "callback",
            "kind": "external_wait",
            "signal_schema": INT,
            "correlation": lit("synthetic", {"kind": "string", "max_length": 20}),
            "expiry_seconds": 3600,
            "assignment_policy_ref": "admins",
        },
        {
            "id": "review",
            "kind": "human_task",
            "mode": "form",
            "assignment_policy_ref": "admins",
            "timeout_seconds": 3600,
            "form_schema": FORM,
        },
    ],
    {
        "kind": "object",
        "fields": [
            {"name": "signal", "value": step_ref("callback")},
            {"name": "form", "value": step_ref("review")},
        ],
    },
    data(
        {
            "kind": "object",
            "fields": [
                {"name": "signal", "schema": INT, "required": True},
                {"name": "form", "schema": FORM, "required": True},
            ],
        }
    ),
    policies=[{"id": "admins", "ref": ref_of(policy)}],
)
compile_package(waits, dependencies=[], catalog={"documents": [policy]})
(OUT / "waits.json").write_text(json.dumps(waits, indent=1, sort_keys=True) + "\n")
(OUT / "waits.policy.json").write_text(json.dumps(policy, indent=1, sort_keys=True) + "\n")

connector, model = catalog[0], catalog[1]
RETRY = {
    "max_attempts": 3,
    "timeout_seconds": 30,
    "backoff_seconds": 5,
    "retry_codes": ["unavailable"],
}
activities = package(
    "fixture-activities",
    NULL,
    NULL,
    [
        {
            "id": "read",
            "kind": "connector_read",
            "connector_ref": "source",
            "operation": "read",
            "target": lit(None, NULL),
            "input": lit(None, NULL),
            "retry": RETRY,
        },
        {
            "id": "think",
            "kind": "agent",
            "model_ref": "model",
            "input": step_ref("read"),
            "instructions": "Synthetic typed input only",
            "output_schema": NULL,
            "limits": {
                "max_steps": 10,
                "timeout_seconds": 30,
                "max_input_tokens": 1000,
                "max_output_tokens": 1000,
                "max_cost_microusd": 100000,
            },
            "retry": {
                "max_attempts": 1,
                "timeout_seconds": 30,
                "backoff_seconds": 0,
                "retry_codes": [],
            },
        },
    ],
    step_ref("think"),
    data(NULL),
    connectors=[{"id": "source", "ref": ref_of(connector)}],
    models=[{"id": "model", "ref": ref_of(model)}],
)
compile_package(activities, dependencies=[], catalog={"documents": [connector, model]})
(OUT / "activities.json").write_text(json.dumps(activities, indent=1, sort_keys=True) + "\n")

child = package(
    "fixture-child",
    INT,
    INT,
    [transform("triple", {"kind": "call", "fn": "multiply", "args": [ref(""), lit(3, INT)]}, INT)],
    step_ref("triple"),
    data(INT),
)
child_sha = sha256(canonical_executable_json(child)).hexdigest()
parent = package(
    "fixture-parent",
    INT,
    INT,
    [{"id": "invoke", "kind": "subworkflow", "workflow_ref": "child", "input": ref("")}],
    step_ref("invoke"),
    data(INT),
    subworkflows=[
        {
            "id": "child",
            "ref": {
                "kind": "workflow",
                "name": "fixture-child",
                "version": "1.0.0",
                "sha256": child_sha,
            },
        }
    ],
)
compile_package(parent, dependencies=[child], catalog={"documents": []})
(OUT / "subworkflow.json").write_text(json.dumps(parent, indent=1, sort_keys=True) + "\n")
(OUT / "subworkflow.child.json").write_text(json.dumps(child, indent=1, sort_keys=True) + "\n")
print("all fixtures compiled")

ITEMS = {"kind": "array", "items": {"kind": "integer", "min": 0, "max": 1000}, "max_items": 5}
scoped = package(
    "fixture-scoped",
    ITEMS,
    {"kind": "array", "items": INT, "max_items": 5},
    [
        transform("base", lit(10, INT), INT),
        {
            "id": "each",
            "kind": "iteration",
            "input": ref(""),
            "max_items": 5,
            "concurrency": 5,
            "body": body(
                [
                    {
                        "id": "pick",
                        "kind": "decision",
                        "condition": {
                            "kind": "call",
                            "fn": "gt",
                            "args": [ref("", {"kind": "iteration_item"}), lit(1, INT)],
                        },
                        "then_body": body(
                            [
                                transform(
                                    "plus",
                                    {
                                        "kind": "call",
                                        "fn": "add",
                                        "args": [
                                            ref("", {"kind": "iteration_item"}),
                                            step_ref("base"),
                                        ],
                                    },
                                    INT,
                                )
                            ],
                            step_ref("plus"),
                            data(INT),
                        ),
                        "else_body": body(
                            [transform("zero", lit(0, INT), INT)], step_ref("zero"), data(INT)
                        ),
                    },
                ],
                step_ref("pick"),
                data(INT),
            ),
        },
    ],
    step_ref("each"),
    data({"kind": "array", "items": INT, "max_items": 5}),
)
compile_package(scoped, dependencies=[], catalog={"documents": []})
(OUT / "scoped.json").write_text(json.dumps(scoped, indent=1, sort_keys=True) + "\n")

AGENT_LIMITS = {
    "max_steps": 10,
    "timeout_seconds": 30,
    "max_input_tokens": 1000,
    "max_output_tokens": 1000,
    "max_cost_microusd": 100000,
}
NO_RETRY = {"max_attempts": 1, "timeout_seconds": 30, "backoff_seconds": 0, "retry_codes": []}
parallel_failure = package(
    "fixture-parallel-failure",
    INT,
    NULL,
    [
        {
            "id": "both",
            "kind": "parallel",
            "join": "all",
            "branches": [
                {
                    "id": "slow",
                    "body": body(
                        [
                            {
                                "id": "think",
                                "kind": "agent",
                                "model_ref": "model",
                                "input": lit(None, NULL),
                                "instructions": "Synthetic typed input only",
                                "output_schema": NULL,
                                "limits": AGENT_LIMITS,
                                "retry": NO_RETRY,
                            }
                        ],
                        step_ref("think"),
                        data(NULL),
                    ),
                },
                {
                    "id": "bad",
                    "body": body(
                        [
                            transform(
                                "divide",
                                {"kind": "call", "fn": "divide", "args": [lit(1, INT), ref("")]},
                                INT,
                            )
                        ],
                        step_ref("divide"),
                        data(INT),
                    ),
                },
            ],
        }
    ],
    lit(None, NULL),
    data(NULL),
    models=[{"id": "model", "ref": ref_of(model)}],
)
compile_package(parallel_failure, dependencies=[], catalog={"documents": [model]})
(OUT / "parallel_failure.json").write_text(
    json.dumps(parallel_failure, indent=1, sort_keys=True) + "\n"
)
print("extra fixtures compiled")

WIDE = {"kind": "array", "items": {"kind": "integer", "min": 0, "max": 1000}, "max_items": 1000}
chain = [
    transform(
        "s0",
        {"kind": "call", "fn": "add", "args": [ref("", {"kind": "iteration_item"}), lit(1, INT)]},
        INT,
    )
]
for index in range(1, 8):
    chain.append(
        transform(
            f"s{index}",
            {"kind": "call", "fn": "add", "args": [step_ref(f"s{index - 1}"), lit(1, INT)]},
            INT,
        )
    )
wide = package(
    "fixture-wide",
    WIDE,
    {"kind": "array", "items": INT, "max_items": 1000},
    [
        {
            "id": "each",
            "kind": "iteration",
            "input": ref(""),
            "max_items": 1000,
            "concurrency": 5,
            "body": body(chain, step_ref("s7"), data(INT)),
        }
    ],
    step_ref("each"),
    data({"kind": "array", "items": INT, "max_items": 1000}),
)
wide["limits"]["max_steps"] = 10000
plan = compile_package(wide, dependencies=[], catalog={"documents": []})
(OUT / "wide.json").write_text(json.dumps(wide, indent=1, sort_keys=True) + "\n")

nested = package(
    "fixture-nested-wait",
    NULL,
    NULL,
    [
        {
            "id": "both",
            "kind": "parallel",
            "join": "all",
            "branches": [
                {
                    "id": "a",
                    "body": body(
                        [
                            {
                                "id": "hold",
                                "kind": "external_wait",
                                "signal_schema": INT,
                                "correlation": lit(
                                    "synthetic", {"kind": "string", "max_length": 20}
                                ),
                                "expiry_seconds": 3600,
                                "assignment_policy_ref": "admins",
                            }
                        ],
                        lit(None, NULL),
                        data(NULL),
                    ),
                },
                {
                    "id": "b",
                    "body": body(
                        [transform("other", lit(None, NULL), NULL)], lit(None, NULL), data(NULL)
                    ),
                },
            ],
        }
    ],
    lit(None, NULL),
    data(NULL),
    policies=[{"id": "admins", "ref": ref_of(policy)}],
)
compile_package(nested, dependencies=[], catalog={"documents": [policy]})
(OUT / "nested_wait.json").write_text(json.dumps(nested, indent=1, sort_keys=True) + "\n")
print("wide and nested fixtures compiled")

DEC2 = {"kind": "decimal", "precision": 10, "scale": 2}
LABEL = {"kind": "string", "max_length": 5}
decimals = package(
    "fixture-decimal-items",
    {"kind": "array", "items": DEC2, "max_items": 5},
    {"kind": "array", "items": LABEL, "max_items": 5},
    [
        transform("limit", lit("10.00", DEC2), DEC2),
        {
            "id": "each",
            "kind": "iteration",
            "input": ref(""),
            "max_items": 5,
            "concurrency": 2,
            "body": body(
                [
                    {
                        "id": "pick",
                        "kind": "decision",
                        "condition": {
                            "kind": "call",
                            "fn": "lt",
                            "args": [ref("", {"kind": "iteration_item"}), step_ref("limit")],
                        },
                        "then_body": body(
                            [transform("below", lit("below", LABEL), LABEL)],
                            step_ref("below"),
                            data(LABEL),
                        ),
                        "else_body": body(
                            [transform("above", lit("above", LABEL), LABEL)],
                            step_ref("above"),
                            data(LABEL),
                        ),
                    },
                ],
                step_ref("pick"),
                data(LABEL),
            ),
        },
    ],
    step_ref("each"),
    data({"kind": "array", "items": LABEL, "max_items": 5}),
)
compile_package(decimals, dependencies=[], catalog={"documents": []})
(OUT / "decimal_items.json").write_text(json.dumps(decimals, indent=1, sort_keys=True) + "\n")
print("decimal fixture compiled")


def agent(sid):
    return {
        "id": sid,
        "kind": "agent",
        "model_ref": "model",
        "input": lit(None, NULL),
        "instructions": "Synthetic typed input only",
        "output_schema": NULL,
        "limits": AGENT_LIMITS,
        "retry": NO_RETRY,
    }


mixed = package(
    "fixture-parallel-mixed",
    INT,
    NULL,
    [
        {
            "id": "both",
            "kind": "parallel",
            "join": "all",
            "branches": [
                {
                    "id": "done",
                    "body": body(
                        [transform("ready", lit(1, INT), INT)], lit(None, NULL), data(NULL)
                    ),
                },
                {"id": "flying", "body": body([agent("fly")], lit(None, NULL), data(NULL))},
                {"id": "unsure", "body": body([agent("guess")], lit(None, NULL), data(NULL))},
                {"id": "queued", "body": body([agent("wait")], lit(None, NULL), data(NULL))},
                {
                    "id": "bad",
                    "body": body(
                        [
                            transform(
                                "divide",
                                {"kind": "call", "fn": "divide", "args": [lit(1, INT), ref("")]},
                                INT,
                            )
                        ],
                        lit(None, NULL),
                        data(NULL),
                    ),
                },
            ],
        }
    ],
    lit(None, NULL),
    data(NULL),
    models=[{"id": "model", "ref": ref_of(model)}],
)
compile_package(mixed, dependencies=[], catalog={"documents": [model]})
(OUT / "parallel_mixed.json").write_text(json.dumps(mixed, indent=1, sort_keys=True) + "\n")
print("mixed parallel fixture compiled")

# An activity in parallel with an external wait: the wait opens while the agent's
# compute runs, so its retention must be retried once that compute has stopped.
parallel_wait = package(
    "fixture-parallel-wait",
    INT,
    NULL,
    [
        {
            "id": "both",
            "kind": "parallel",
            "join": "all",
            "branches": [
                {"id": "slow", "body": body([agent("think")], lit(None, NULL), data(NULL))},
                {
                    "id": "held",
                    "body": body(
                        [
                            {
                                "id": "hold",
                                "kind": "external_wait",
                                "signal_schema": INT,
                                "correlation": lit(
                                    "synthetic", {"kind": "string", "max_length": 20}
                                ),
                                "expiry_seconds": 3600,
                                "assignment_policy_ref": "admins",
                            }
                        ],
                        lit(None, NULL),
                        data(NULL),
                    ),
                },
            ],
        }
    ],
    lit(None, NULL),
    data(NULL),
    models=[{"id": "model", "ref": ref_of(model)}],
    policies=[{"id": "admins", "ref": ref_of(policy)}],
)
compile_package(parallel_wait, dependencies=[], catalog={"documents": [model, policy]})
(OUT / "parallel_wait.json").write_text(json.dumps(parallel_wait, indent=1, sort_keys=True) + "\n")
print("parallel wait fixture compiled")

# Pure iteration work beside an external wait: a wait that cannot be retained must
# not throttle the sibling's progress.
BUSY = {"kind": "array", "items": {"kind": "integer", "min": 0, "max": 1000}, "max_items": 64}
busy = package(
    "fixture-parallel-busy",
    BUSY,
    NULL,
    [
        {
            "id": "both",
            "kind": "parallel",
            "join": "all",
            "branches": [
                {
                    "id": "held",
                    "body": body(
                        [
                            {
                                "id": "hold",
                                "kind": "external_wait",
                                "signal_schema": INT,
                                "correlation": lit(
                                    "synthetic", {"kind": "string", "max_length": 20}
                                ),
                                "expiry_seconds": 3600,
                                "assignment_policy_ref": "admins",
                            }
                        ],
                        lit(None, NULL),
                        data(NULL),
                    ),
                },
                {
                    "id": "busy",
                    "body": body(
                        [
                            {
                                "id": "each",
                                "kind": "iteration",
                                "input": ref(""),
                                "max_items": 64,
                                "concurrency": 8,
                                "body": body(chain, step_ref("s7"), data(INT)),
                            }
                        ],
                        lit(None, NULL),
                        data(NULL),
                    ),
                },
            ],
        }
    ],
    lit(None, NULL),
    data(NULL),
    policies=[{"id": "admins", "ref": ref_of(policy)}],
)
busy["limits"]["max_steps"] = 1000
compile_package(busy, dependencies=[], catalog={"documents": [policy]})
(OUT / "parallel_busy.json").write_text(json.dumps(busy, indent=1, sort_keys=True) + "\n")
print("parallel busy fixture compiled")

# The same shape with a retrying agent: a sibling's retry wake-up must not wait
# behind the back-off of a wait stuck at quota.
retrying = copy.deepcopy(parallel_wait)
retrying["package_id"] = "fixture-parallel-retry"
retrying["body"]["steps"][0]["branches"][0]["body"]["steps"][0]["retry"] = {
    "max_attempts": 3,
    "timeout_seconds": 30,
    "backoff_seconds": 2,
    "retry_codes": ["unavailable"],
}
compile_package(retrying, dependencies=[], catalog={"documents": [model, policy]})
(OUT / "parallel_retry.json").write_text(json.dumps(retrying, indent=1, sort_keys=True) + "\n")
print("parallel retry fixture compiled")

# Task 9: one connector read with a 2 s attempt timeout, so lease and delivery
# expiry is observed in real database time without editing any deadline column.
FAST = {
    "max_attempts": 3,
    "timeout_seconds": 2,
    "backoff_seconds": 0,
    "retry_codes": ["unavailable", "timeout_before_dispatch"],
}
activity_read = package(
    "fixture-activity-read",
    NULL,
    NULL,
    [
        {
            "id": "read",
            "kind": "connector_read",
            "connector_ref": "source",
            "operation": "read",
            "target": lit(None, NULL),
            "input": lit(None, NULL),
            "retry": FAST,
        }
    ],
    step_ref("read"),
    data(NULL),
    connectors=[{"id": "source", "ref": ref_of(connector)}],
)
compile_package(activity_read, dependencies=[], catalog={"documents": [connector]})
(OUT / "activity_read.json").write_text(json.dumps(activity_read, indent=1, sort_keys=True) + "\n")

# Task 9: an agent whose model declares one read tool, exposed by the step.
model_tools = copy.deepcopy(model)
model_tools["name"] = "model-tools-fixture"  # model-fixture 1.0.0 is published elsewhere
model_tools["definition"]["read_tools"] = [
    {"connector_ref": ref_of(connector), "operation": "read", "retry": RETRY}
]
agent_tools = package(
    "fixture-agent-tools",
    NULL,
    NULL,
    [
        {
            "id": "think",
            "kind": "agent",
            "model_ref": "model",
            "input": lit(None, NULL),
            "instructions": "Synthetic typed input only",
            "output_schema": NULL,
            "read_tools": [{"connector_ref": "source", "operation": "read", "retry": RETRY}],
            "limits": AGENT_LIMITS,
            "retry": NO_RETRY,
        }
    ],
    step_ref("think"),
    data(NULL),
    connectors=[{"id": "source", "ref": ref_of(connector)}],
    models=[{"id": "model", "ref": ref_of(model_tools)}],
)
compile_package(agent_tools, dependencies=[], catalog={"documents": [connector, model_tools]})
(OUT / "agent_tools.json").write_text(json.dumps(agent_tools, indent=1, sort_keys=True) + "\n")
(OUT / "agent_tools.model.json").write_text(
    json.dumps(model_tools, indent=1, sort_keys=True) + "\n"
)
print("activity fixtures compiled")

# Task 9: a connector read with a 120 s timeout, so the 60 s lease cap is what binds.
activity_slow = copy.deepcopy(activity_read)
activity_slow["package_id"] = "fixture-activity-slow"
activity_slow["body"]["steps"][0]["retry"] = {**FAST, "timeout_seconds": 120}
compile_package(activity_slow, dependencies=[], catalog={"documents": [connector]})
(OUT / "activity_slow.json").write_text(json.dumps(activity_slow, indent=1, sort_keys=True) + "\n")
print("slow activity fixture compiled")

# Task 11: the waits package with a 2 s timer and a 3 s external-wait expiry, and
# one whose 5 s execution deadline passes while its 60 s timer waits. The sweeper's
# due waits and deadlines then come in real database time, never by column edits.
waits_fast = copy.deepcopy(waits)
waits_fast["package_id"] = "fixture-waits-fast"
waits_fast["body"]["steps"][0]["delay_seconds"] = 2
waits_fast["body"]["steps"][1]["expiry_seconds"] = 3
compile_package(waits_fast, dependencies=[], catalog={"documents": [policy]})
(OUT / "waits_fast.json").write_text(json.dumps(waits_fast, indent=1, sort_keys=True) + "\n")
waits_deadline = copy.deepcopy(waits)
waits_deadline["package_id"] = "fixture-waits-deadline"
waits_deadline["limits"] = {**waits["limits"], "elapsed_seconds": 5}
compile_package(waits_deadline, dependencies=[], catalog={"documents": [policy]})
(OUT / "waits_deadline.json").write_text(
    json.dumps(waits_deadline, indent=1, sort_keys=True) + "\n"
)
print("fast wait fixtures compiled")

# Task 11: the connector read with a 4 s execution deadline, and one whose retry
# back-off (an hour) lies far beyond that deadline.
activity_deadline = copy.deepcopy(activity_read)
activity_deadline["package_id"] = "fixture-activity-deadline"
activity_deadline["limits"] = {**activity_read["limits"], "elapsed_seconds": 4}
compile_package(activity_deadline, dependencies=[], catalog={"documents": [connector]})
(OUT / "activity_deadline.json").write_text(
    json.dumps(activity_deadline, indent=1, sort_keys=True) + "\n"
)
activity_backoff = copy.deepcopy(activity_deadline)
activity_backoff["package_id"] = "fixture-activity-backoff"
activity_backoff["body"]["steps"][0]["retry"] = {**FAST, "backoff_seconds": 3600}
compile_package(activity_backoff, dependencies=[], catalog={"documents": [connector]})
(OUT / "activity_backoff.json").write_text(
    json.dumps(activity_backoff, indent=1, sort_keys=True) + "\n"
)
print("deadline activity fixtures compiled")

# Task 11: the connector read with a 10 microdollar budget, spent by one reservation.
activity_budget = copy.deepcopy(activity_read)
activity_budget["package_id"] = "fixture-activity-budget"
activity_budget["limits"] = {**activity_read["limits"], "max_cost_microusd": 10}
compile_package(activity_budget, dependencies=[], catalog={"documents": [connector]})
(OUT / "activity_budget.json").write_text(
    json.dumps(activity_budget, indent=1, sort_keys=True) + "\n"
)
print("budget activity fixture compiled")
