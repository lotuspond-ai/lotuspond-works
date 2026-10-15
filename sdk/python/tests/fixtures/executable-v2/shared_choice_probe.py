"""Independent bounded-process probes for shared choice traversal, not golden generation."""

import json
import sys
from pathlib import Path

from lotuspond_sdk.executable.compiler import _item, _select, _symbol_fingerprint

# Count actual compiler execution independently of its own work-budget accounting.
# The 32-node Fibonacci DAG expands to millions of visits without memoization;
# bounded compilation currently uses fewer than 150,000 compiler line events.
# Leave headroom for ordinary compiler changes without depending on runner speed.
compiler_filename = _select.__code__.co_filename
line_events = 0


def bounded_work(frame, event, arg):
    global line_events
    if frame.f_code.co_filename != compiler_filename:
        return None
    if event == "line":
        line_events += 1
        assert line_events <= 500_000, "shared-choice compiler work exceeded 500000 line events"
    return bounded_work


sys.settrace(bounded_work)
mode = sys.argv[1]
if mode == "public_select":
    sys.path.insert(0, str(Path(sys.argv[2]) / "sdk/python/tests/executable"))
    from test_compiler import compile_case, ref, target_case

    import lotuspond_sdk.executable as api

    def case(name):
        return json.loads(
            (Path(sys.argv[2]) / "contracts/examples/executable-v2" / (name + ".json")).read_text()
        )

    root, catalog = target_case(case, lambda: case("catalog"))
    obj = {
        "kind": "object",
        "fields": [{"name": "x", "required": True, "schema": {"kind": "integer"}}],
    }
    for operation in catalog["documents"][0]["definition"]["operations"]:
        operation["target_schema"] = {"kind": "integer"}
    root["connectors"][0]["ref"]["sha256"] = api.catalog_document_digest(catalog["documents"][0])
    steps = [
        {
            "id": f"v{i}",
            "kind": "transform",
            "mapping": {"kind": "literal", "schema": obj, "value": {"x": i}},
            "output_schema": obj,
        }
        for i in range(2)
    ]
    for i in range(2, 32):
        steps.append(
            {
                "id": f"v{i}",
                "kind": "decision",
                "condition": {"kind": "literal", "schema": {"kind": "boolean"}, "value": True},
                "then_body": {
                    "steps": [],
                    "result": ref(f"v{i - 1}"),
                    "result_schema": {"kind": "data", "schema": obj},
                },
                "else_body": {
                    "steps": [],
                    "result": ref(f"v{i - 2}"),
                    "result_schema": {"kind": "data", "schema": obj},
                },
            }
        )
    root["body"]["steps"][0]["target"] = ref("v31", "/x")
    root["body"]["steps"][-1]["target"] = ref("v31", "/x")
    root["body"]["steps"] = steps + root["body"]["steps"]
    assert compile_case(root, catalog=catalog).maximum_step_attempts == 67
else:
    values = [
        (("object", (("x", ("literal", str(i))),)))
        if mode == "select"
        else ("array", (("literal", str(i)),))
        for i in range(2)
    ]
    for i in range(2, 32):
        values.append(("choice", ("selector", ("probe", str(i)), ()), (values[-1], values[-2])))
    output = _select(values[-1], "x") if mode == "select" else _item(values[-1], "$item:consumer")
    assert output[0] == "choice"
    assert len(_symbol_fingerprint(output)) == 64
sys.settrace(None)
assert line_events > 0, "compiler work guard did not observe the probe"
print("PASS")
