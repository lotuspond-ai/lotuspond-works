"""Static inference never evaluates expressions or creates handle origins."""

import pytest

from lotuspond_sdk.executable import ExecutableError
from lotuspond_sdk.executable.expressions import TypeEnvironment, infer_mapping, presence_guard
from lotuspond_sdk.executable.types import (
    DataType,
    FlowArrayType,
    FlowObjectType,
    HandleType,
    SourceSymbol,
    assert_assignable,
    parse_schema,
)

STRING = {"kind": "string", "max_length": 20}
INTEGER = {"kind": "integer"}
BOOLEAN = {"kind": "boolean"}


def literal(value, schema=INTEGER):
    return {"kind": "literal", "schema": schema, "value": value}


def ref(pointer="", source=None):
    return {"kind": "ref", "source": source or {"kind": "package_input"}, "pointer": pointer}


def call(fn, *args, **kwargs):
    return {"kind": "call", "fn": fn, "args": list(args), **kwargs}


def env():
    return TypeEnvironment(
        input_schema={
            "kind": "object",
            "fields": [
                {"name": "a/b~c", "schema": STRING, "required": False},
                {"name": "nullable", "schema": {"kind": "null"}, "required": True},
            ],
        }
    )


def test_optional_reference_requires_default_or_direct_guard():
    optional = ref("/a~1b~0c")
    inferred = infer_mapping(optional, env())
    assert inferred.may_be_missing is True
    with pytest.raises(ExecutableError) as error:
        assert_assignable(inferred, {"kind": "data", "schema": STRING})
    assert error.value.code == "unavailable_value"
    defaulted = infer_mapping(call("coalesce", optional, literal("x", STRING)), env())
    assert defaulted.may_be_missing is False
    guarded = env()
    guarded.present_refs = presence_guard(call("exists", optional))
    assert infer_mapping(optional, guarded).may_be_missing is False
    assert infer_mapping(call("exists", ref("/nullable")), env()).schema.kind == "boolean"
    assert presence_guard(call("not", call("exists", optional))) == frozenset()


@pytest.mark.parametrize("pointer", ["/bad~2", "not-pointer", "/missing", "/a~1b~0c/x"])
def test_invalid_pointer(pointer):
    with pytest.raises(ExecutableError):
        infer_mapping(ref(pointer), env())


@pytest.mark.parametrize(
    "source",
    [
        {"kind": "step", "id": "future"},
        {"kind": "step", "id": "branch-private"},
        {"kind": "iteration_item"},
        {"kind": "iteration_index"},
        {"kind": "environment"},
    ],
)
def test_unavailable_lexical_sources(source):
    with pytest.raises(ExecutableError):
        infer_mapping(ref(source=source), env())


def test_handle_containers_preserve_origins_and_require_presence():
    handle = HandleType("action_intent", SourceSymbol("action_intent", "propose"))
    environment = env()
    environment.step_outputs = {"propose": handle}
    h = ref(source={"kind": "step", "id": "propose"})
    expected = {"kind": "handle", "handle_kind": "action_intent"}
    result = infer_mapping({"kind": "array", "items": [h], "item_schema": expected}, environment)
    assert isinstance(result, FlowArrayType)
    assert result.items.source == handle.source
    obj = infer_mapping({"kind": "object", "fields": [{"name": "intent", "value": h}]}, environment)
    assert isinstance(obj, FlowObjectType)
    assert obj.fields["intent"].source == handle.source
    environment.step_outputs["all"] = result
    indexed = ref("/0", {"kind": "step", "id": "all"})
    indexed_type = infer_mapping(indexed, environment)
    assert indexed_type.may_be_missing
    assert indexed_type.source == handle.source
    with pytest.raises(ExecutableError):
        assert_assignable(indexed_type, expected)
    environment.present_refs = presence_guard(call("exists", indexed))
    assert_assignable(infer_mapping(indexed, environment), expected)
    with pytest.raises(ExecutableError):
        infer_mapping(
            {
                "kind": "array",
                "items": [literal({}, {"kind": "object", "fields": []})],
                "item_schema": expected,
            },
            environment,
        )


@pytest.mark.parametrize("pointer", ["/-1", "/01", "/+1", "/1.0", "/1", "/*"])
def test_array_index_is_canonical_and_within_bound(pointer):
    environment = TypeEnvironment(input_schema={"kind": "array", "items": INTEGER, "max_items": 1})
    with pytest.raises(ExecutableError):
        infer_mapping(ref(pointer), environment)


@pytest.mark.parametrize(
    "fn,args,expected",
    [
        ("concat", [literal("a", STRING), literal("b", STRING)], "string"),
        *[
            (fn, [literal(1), literal(0)], "integer")
            for fn in ("add", "subtract", "multiply", "divide")
        ],
        *[(fn, [literal(1), literal(2)], "boolean") for fn in ("eq", "ne", "lt", "le", "gt", "ge")],
        ("and", [literal(True, BOOLEAN), literal(False, BOOLEAN)], "boolean"),
        ("or", [literal(True, BOOLEAN), literal(False, BOOLEAN)], "boolean"),
        ("not", [literal(True, BOOLEAN)], "boolean"),
    ],
)
def test_fixed_operation_signatures_without_constant_folding(fn, args, expected):
    assert infer_mapping(call(fn, *args), env()).schema.kind == expected


@pytest.mark.parametrize(
    "expression",
    [
        call("eval", literal(1)),
        call("add", literal(1), literal(1.0, {"kind": "number"})),
        call("not", literal(1)),
        call("exists", literal(1)),
        call("coalesce", literal(1), literal("x", STRING)),
        call("add", literal(1)),
        call("add", literal(1), literal(2), scale=0),
        {"kind": "literal", "schema": INTEGER, "value": 1, "extra": True},
    ],
)
def test_invalid_signatures_and_closed_mappings(expression):
    with pytest.raises(ExecutableError):
        infer_mapping(expression, env())


def test_decimal_arithmetic_declares_scale_rounding():
    decimal = {"kind": "decimal", "precision": 4, "scale": 2}
    args = [literal("1.00", decimal)] * 2
    result = infer_mapping(call("divide", *args, scale=3, rounding="half_even"), env())
    assert result.schema.scale == 3
    for kw in ({}, {"scale": True, "rounding": "reject"}, {"scale": 2, "rounding": "random"}):
        with pytest.raises(ExecutableError):
            infer_mapping(call("divide", *args, **kw), env())


def test_expression_exact_node_and_depth_boundaries():
    for size in (63, 64):
        raw = {
            "kind": "array",
            "items": [literal(1)] * size,
            "item_schema": {"kind": "data", "schema": INTEGER},
        }
        if size == 63:
            assert infer_mapping(raw, env()).max_items == 63
        else:
            with pytest.raises(ExecutableError):
                infer_mapping(raw, env())
    raw = literal(True, BOOLEAN)
    for _ in range(7):
        raw = call("not", raw)
    assert infer_mapping(raw, env()).schema.kind == "boolean"
    with pytest.raises(ExecutableError):
        infer_mapping(call("not", raw), env())


def test_assignment_checks_value_bounds_and_no_implicit_numeric_cast():
    for actual, expected in [
        (STRING, {"kind": "string", "max_length": 2}),
        (INTEGER, {"kind": "number"}),
        (INTEGER, {"kind": "integer", "min": 0}),
    ]:
        with pytest.raises(ExecutableError):
            assert_assignable(DataType(parse_schema(actual)), {"kind": "data", "schema": expected})


def test_handle_array_alias_and_unknown_iteration_keep_distinct_origins():
    from lotuspond_sdk.executable.types import OriginChoice, iteration_item_flow, select_array_item

    first = HandleType("action_intent", SourceSymbol("action_intent", "first"))
    second = HandleType("action_intent", SourceSymbol("action_intent", "second"))
    environment = env()
    environment.step_outputs = {"first": first, "second": second}
    array = infer_mapping(
        {
            "kind": "array",
            "items": [ref(source={"kind": "step", "id": name}) for name in ("first", "second")],
            "item_schema": {"kind": "handle", "handle_kind": "action_intent"},
        },
        environment,
    )
    assert select_array_item(array, 0).source == first.source
    assert select_array_item(array, 1).source == second.source
    assert isinstance(array.items.source, OriginChoice)
    selected = iteration_item_flow(array, occurrence_path=("loop",))
    assert selected.source != first.source
    assert selected.source != second.source
    assert isinstance(selected.source, OriginChoice)
    assert selected.source.options == (first.source, second.source)
    assert selected.source.selection_path == ("loop",)
    assert not selected.may_be_missing
    assert iteration_item_flow(array, occurrence_path=("other",)).source != selected.source


def test_array_item_inference_preserves_declared_union_of_value_bounds():
    environment = env()
    result = infer_mapping(
        {
            "kind": "array",
            "items": [literal("x", {"kind": "string", "max_length": 1}), literal("hello", STRING)],
            "item_schema": {"kind": "data", "schema": STRING},
        },
        environment,
    )
    assert_assignable(
        result, {"kind": "array", "max_items": 2, "items": {"kind": "data", "schema": STRING}}
    )


def test_constructed_and_mutated_mapping_instances_revalidated():
    from lotuspond_sdk.executable.expressions import CallMapping, parse_mapping

    raw = parse_mapping(call("not", literal(True, BOOLEAN)))
    raw.fn = "eval"
    with pytest.raises(ExecutableError):
        infer_mapping(raw, env())
    raw = CallMapping.model_construct(
        kind="call", fn="not", args=[literal(True, BOOLEAN)], scale=True, rounding=None
    )
    with pytest.raises(ExecutableError):
        infer_mapping(raw, env())


def test_optional_parent_guard_cannot_prove_optional_child():
    schema = {
        "kind": "object",
        "fields": [
            {
                "name": "outer",
                "required": False,
                "schema": {
                    "kind": "object",
                    "fields": [{"name": "inner", "required": False, "schema": STRING}],
                },
            }
        ],
    }
    environment = TypeEnvironment(
        input_schema=schema, present_refs=presence_guard(call("exists", ref("/outer")))
    )
    assert infer_mapping(ref("/outer/inner"), environment).may_be_missing


def test_symbolic_selection_preserves_position_and_multiplicity():
    from lotuspond_sdk.executable.types import iteration_item_flow

    a = HandleType("action_intent", SourceSymbol("action_intent", "a"))
    b = HandleType("action_intent", SourceSymbol("action_intent", "b"))
    left = FlowArrayType(a, 3, elements=(a, a, b))
    right = FlowArrayType(a, 3, elements=(a, b, b))
    assert (
        iteration_item_flow(left, occurrence_path=("loop",)).source
        != iteration_item_flow(right, occurrence_path=("loop",)).source
    )


def test_mapping_number_literal_normalizes_before_complete_jcs_measurement():
    from lotuspond_sdk.executable import decode_executable_bytes
    from lotuspond_sdk.executable._canonical import jcs_bytes
    from lotuspond_sdk.executable.expressions import MAPPING_MODELS, parse_mapping
    from lotuspond_sdk.executable.input import project_json

    for value in (1.0, -0.0, 1e20, float.fromhex("0x1.fffffffffffffp+1023")):
        parsed = parse_mapping(literal(value, {"kind": "number"}))
        first = jcs_bytes(project_json(parsed, model_types=MAPPING_MODELS))
        reread = parse_mapping(decode_executable_bytes(first))
        assert first == jcs_bytes(project_json(reread, model_types=MAPPING_MODELS))


def test_step_data_refs_resolve_environment_definitions():
    environment = TypeEnvironment(
        input_schema=INTEGER,
        step_outputs={"earlier": DataType(parse_schema({"kind": "ref", "name": "text"}))},
        schema_definitions=[{"name": "text", "schema": STRING}],
    )
    assert (
        infer_mapping(ref(source={"kind": "step", "id": "earlier"}), environment).schema.kind
        == "string"
    )


def test_empty_handle_array_has_no_origin_and_no_selectable_item():
    from lotuspond_sdk.executable.types import iteration_item_flow, select_array_item

    expected = {"kind": "handle", "handle_kind": "action_intent"}
    result = infer_mapping({"kind": "array", "items": [], "item_schema": expected}, env())
    assert result.items is None
    assert_assignable(result, {"kind": "array", "items": expected, "max_items": 10})
    for select in (
        lambda: select_array_item(result, 0),
        lambda: iteration_item_flow(result, occurrence_path=("loop",)),
    ):
        with pytest.raises(ExecutableError):
            select()


def test_optional_object_slots_preserve_cross_field_selection_correlation():
    from lotuspond_sdk.executable.types import iteration_item_flow

    handles = {
        name: HandleType("action_intent", SourceSymbol("action_intent", name))
        for name in ("a", "b", "c")
    }
    records = tuple(
        FlowObjectType({name: handles[value] for name, value in row.items()})
        for row in (
            {"left": "a", "right": "a"},
            {"right": "b"},
            {"left": "b", "right": "c"},
            {"left": "c"},
        )
    )
    expected = {
        "kind": "object",
        "fields": [
            {
                "name": name,
                "required": False,
                "schema": {"kind": "handle", "handle_kind": "action_intent"},
            }
            for name in ("left", "right")
        ],
    }
    from lotuspond_sdk.executable.types import parse_export

    result = iteration_item_flow(
        FlowArrayType(records[0], 4, elements=records, item_schema=parse_export(expected)),
        occurrence_path=("loop",),
    )
    assert result.fields["left"].source != result.fields["right"].source
    assert result.fields["left"].may_be_missing


def test_homogeneous_aliases_and_operation_occurrence_templates_differ():
    from lotuspond_sdk.executable.types import iteration_item_flow, select_array_item

    alias = HandleType("action_intent", SourceSymbol("action_intent", "outside"))
    array = FlowArrayType(alias, 10)
    assert select_array_item(array, 0).source == alias.source
    assert iteration_item_flow(array, occurrence_path=("loop",)).source == alias.source
    template = HandleType(
        "action_intent", SourceSymbol("action_intent", "inside", ("loop", "$item:loop"))
    )
    assert select_array_item(
        FlowArrayType(template, 10, iteration_id="loop"), 0
    ).source.occurrence_path == ("loop", "0")
    assert select_array_item(
        FlowArrayType(template, 10, iteration_id="loop"), 1
    ).source.occurrence_path == ("loop", "1")


def test_inner_iteration_alias_cannot_bind_outer_iteration_slot():
    from lotuspond_sdk.executable.types import iteration_item_flow, select_array_item

    outer = HandleType(
        "action_intent", SourceSymbol("action_intent", "outer-intent", ("$item:outer",))
    )
    inner_results = FlowArrayType(outer, 5, iteration_id="inner")
    assert select_array_item(inner_results, 0).source == outer.source
    assert iteration_item_flow(inner_results, occurrence_path=("consumer",)).source == outer.source
    outer_results = FlowArrayType(outer, 5, iteration_id="outer")
    assert select_array_item(outer_results, 0).source.occurrence_path == ("0",)
