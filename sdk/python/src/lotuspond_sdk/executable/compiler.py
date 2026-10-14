"""Lexical static compilation. No expression evaluation or external execution."""

from dataclasses import dataclass, field, replace

from ._canonical import jcs_bytes, sha256_hex
from .catalog import CATALOG_MODELS
from .dependencies import resolve_dependencies
from .errors import ExecutableError
from .expressions import ReferenceKey, TypeEnvironment, infer_mapping, presence_guard
from .input import project_json
from .package import PACKAGE_MODELS
from .plan import CompiledPlan, canonical_plan_json, flow_projection, origin_projection
from .types import (
    DataType,
    FlowArrayType,
    FlowObjectType,
    HandleType,
    SourceSymbol,
    assert_assignable,
    iteration_item_flow,
    merge_flow_elements,
    parse_export,
    resolve_schema,
)


def _json(value):
    return project_json(value, model_types=(*PACKAGE_MODELS, *CATALOG_MODELS))


def _digest(value):
    return sha256_hex(jcs_bytes(value))


def _data(schema, definitions=()):
    return parse_export({"kind": "data", "schema": _json(schema)}, definitions)


def _leaves(origin):
    if type(origin) is SourceSymbol:
        return [origin]
    return [leaf for option in origin.options if option is not None for leaf in _leaves(option)]


@dataclass
class _Scope:
    input_schema: object
    step_outputs: dict = field(default_factory=dict)
    iteration_item: object = None
    schema_definitions: list = field(default_factory=list)
    present_refs: frozenset = frozenset()
    value_outputs: dict = field(default_factory=dict)
    input_identity: tuple = ()
    item_identity: tuple | None = None

    def types(self):
        return TypeEnvironment(
            self.input_schema,
            self.step_outputs,
            self.iteration_item,
            self.schema_definitions,
            self.present_refs,
        )


def _selector(path, bindings=()):
    """A selection identity includes its ordered owner bindings, not only its path."""
    return ("selector", path, bindings)


def _bind_selector(selector, owner, binding):
    _, path, bindings = selector
    slot = f"$item:{owner}"
    if slot not in path:
        return selector
    index = path.index(slot)
    return _selector((*path[:index], *binding, *path[index + 1 :]), (*bindings, (owner, binding)))


@dataclass
class _SymbolWork:
    """One bounded symbolic operation; retained nodes prevent ephemeral ID reuse."""

    remaining: int = 262144
    fingerprints: dict = field(default_factory=dict)

    def spend(self, count=1):
        self.remaining -= count
        if self.remaining < 0:
            raise ExecutableError("expansion_exceeded")


def _choice(selector, options, work=None):
    work = _SymbolWork() if work is None else work
    work.spend(len(options))
    first = _symbol_fingerprint(options[0], work)
    return (
        options[0]
        if all(_symbol_fingerprint(x, work) == first for x in options)
        else ("choice", selector, options)
    )


def _transform_symbol(value, *, owner=None, binding=(), selections=None, work=None):
    """Transform a shared DAG once per node and full caller-selected context.

    Object IDs are only ephemeral memo keys; no IDs enter semantic fingerprints.
    Each invocation has its own owner/binding or restriction context and memo.
    """
    work = _SymbolWork() if work is None else work
    memo = {}
    stack = [(value, False)]
    while stack:
        node, ready = stack.pop()
        if type(node) is not tuple or id(node) in memo:
            continue
        work.spend()
        if owner is not None and node and node[0] == "selector":
            memo[id(node)] = _bind_selector(node, owner, binding)
            continue
        replacement = node
        if selections is not None and node and node[0] == "choice" and node[1] in selections:
            replacement = node[2][selections[node[1]]]
        children = (replacement,) if replacement is not node else node
        if not ready:
            work.spend(len(children))
            stack.append((node, True))
            stack.extend(
                (child, False)
                for child in reversed(children)
                if type(child) is tuple and id(child) not in memo
            )
            continue

        def result(child):
            if type(child) is tuple:
                return memo[id(child)]
            if owner is not None and child == f"$item:{owner}":
                return ("binding", owner, binding)
            return child

        memo[id(node)] = (
            result(replacement)
            if replacement is not node
            else tuple(result(child) for child in node)
        )
    return memo[id(value)]


def _bind(value, owner, path, work=None):
    return _transform_symbol(value, owner=owner, binding=path, work=work)


def _symbol_fingerprint(value, work=None):
    """Stable domain-tagged content identity without expanding shared references."""
    work = _SymbolWork() if work is None else work
    memo, stack = work.fingerprints, [(value, False)]
    while stack:
        node, ready = stack.pop()
        if type(node) is not tuple or id(node) in memo:
            continue
        work.spend()
        if not ready:
            stack.append((node, True))
            stack.extend(
                (child, False)
                for child in reversed(node)
                if type(child) is tuple and id(child) not in memo
            )
            continue
        work.spend(len(node))
        fingerprint = _digest(
            [
                "tuple",
                *[
                    memo[id(child)][1] if type(child) is tuple else _digest(["atom", child])
                    for child in node
                ],
            ]
        )
        memo[id(node)] = (node, fingerprint)
    return memo[id(value)][1]


def _distribute(value, selection, *, item, work=None):
    """Project a choice DAG once per node in this fixed token/slot context.

    The context-local memo retains sharing. Fingerprinting and owner binding use
    the same work budget, including intermediates, for the entire operation.
    """
    work = _SymbolWork() if work is None else work
    memo, stack = {}, [(value, False)]
    while stack:
        node, ready = stack.pop()
        if id(node) in memo:
            continue
        work.spend()
        kind = node[0]
        if kind == "choice" and not ready:
            work.spend(len(node[2]))
            stack.append((node, True))
            stack.extend((option, False) for option in reversed(node[2]))
            continue
        if kind == "choice":
            result = _choice(node[1], tuple(memo[id(x)] for x in node[2]), work)
        elif kind == "repeat":
            result = _bind(node[2], node[1], (selection,), work)
        elif item:
            result = (
                _choice(_selector((selection,)), node[1], work)
                if kind == "array" and node[1]
                else ("item", node, (selection,))
            )
        elif kind == "object":
            work.spend(len(node[1]))
            result = dict(node[1]).get(selection, ("absent",))
        elif kind == "array":
            index = int(selection)
            result = node[1][index] if index < len(node[1]) else ("absent",)
        else:
            result = ("pointer", node, selection)
        memo[id(node)] = result
    return memo[id(value)]


def _select(value, token):
    return _distribute(value, token, item=False)


def _item(value, slot):
    return _distribute(value, slot, item=True)


def _restrict(value, selections):
    return _transform_symbol(value, selections=selections)


def _origin_options(origin, selections=()):
    if type(origin) is SourceSymbol:
        return [(origin, selections)]
    return [
        (leaf, context)
        for i, option in enumerate(origin.options)
        if option is not None
        for leaf, context in _origin_options(
            option, (*selections, (_selector(origin.selection_path, origin.selection_bindings), i))
        )
    ]


class _Compiler:
    def __init__(self, closure):
        self.closure = closure
        self.steps, self.regions, self.edges, self.obligations = [], [], [], []
        self.counts = {}
        self.intents = {}
        self.targets = {}
        self.values = {}
        self.loop_guards = {}
        self.selector_templates = {}

    def value(self, mapping, env):
        kind = mapping.kind
        if kind == "literal":
            return ("literal", _digest(_json(mapping)["value"]))
        if kind == "ref":
            source = mapping.source
            if source.kind == "package_input":
                value = env.input_identity
            elif source.kind == "step":
                value = env.value_outputs[source.id]
            elif source.kind == "iteration_item":
                value = env.item_identity
            else:
                value = ("index", env.item_identity)
            for token in mapping.pointer.split("/")[1:]:
                value = _select(value, token.replace("~1", "/").replace("~0", "~"))
            return value
        if kind == "object":
            return ("object", tuple((x.name, self.value(x.value, env)) for x in mapping.fields))
        if kind == "array":
            return ("array", tuple(self.value(x, env) for x in mapping.items))
        return (
            "call",
            mapping.fn,
            tuple(self.value(x, env) for x in mapping.args),
            mapping.scale,
            mapping.rounding,
        )

    def verification_binding(self, package, intent, verification):
        write, _, write_pin = self.operation(
            package, intent.connector_ref, intent.operation, "write"
        )
        _, _, read_pin = self.operation(
            package, verification.connector_ref, verification.read_operation, "read"
        )
        if (
            write_pin != read_pin
            or write.reconciliation.read_operation != verification.read_operation
        ):
            raise ExecutableError("intent_mismatch")

    def check_catalog(self):
        for document in self.closure.catalog.values():
            definition = document.definition
            definitions = document.schemas
            if document.kind == "connector":
                for operation in definition.operations:
                    if operation.reconciliation.mode != "read_back":
                        continue
                    read = next(
                        x
                        for x in definition.operations
                        if x.id == operation.reconciliation.read_operation
                    )
                    env = TypeEnvironment(operation.input_schema, schema_definitions=definitions)
                    self.rules(
                        operation.reconciliation.absence_rules, read.output_schema, definitions, env
                    )
                    self.rules(
                        operation.reconciliation.effect_rules, read.output_schema, definitions, env
                    )
            elif document.kind == "evaluation":
                env = TypeEnvironment(definition.input_schema, schema_definitions=definitions)
                for named_rule in definition.rules:
                    self.rules([named_rule.rule], definition.input_schema, definitions, env)
            elif document.kind in ("agent", "model"):
                for tool in definition.read_tools:
                    connector = self.document(tool.connector_ref)
                    operation = next(
                        (x for x in connector.definition.operations if x.id == tool.operation), None
                    )
                    if operation is None or operation.effect != "read":
                        raise ExecutableError("unsupported_operation")
                if document.kind == "agent":
                    model = self.document(definition.model_ref)
                    assert_assignable(
                        DataType(resolve_schema(definition.input_schema, definitions)),
                        _data(model.definition.input_schema, model.schemas),
                    )
                    assert_assignable(
                        DataType(resolve_schema(model.definition.output_schema, model.schemas)),
                        _data(definition.output_schema, definitions),
                    )
                    allowed = {
                        (x.connector_ref.sha256, x.operation) for x in model.definition.read_tools
                    }
                    if any(
                        (x.connector_ref.sha256, x.operation) not in allowed
                        for x in definition.read_tools
                    ):
                        raise ExecutableError("unsupported_operation")
                    for name in type(definition.limits).model_fields:
                        if getattr(definition.limits, name) > getattr(
                            model.definition.limits, name
                        ):
                            raise ExecutableError("expansion_exceeded")

    def document(self, ref):
        return self.closure.catalog[(ref.kind, ref.name, ref.version)]

    def declaration(self, package, family, local):
        return next(x for x in getattr(package, family) if x.id == local)

    def operation(self, package, local, name, effect):
        declaration = self.declaration(package, "connectors", local)
        document = self.document(declaration.ref)
        operation = next((x for x in document.definition.operations if x.id == name), None)
        if operation is None or operation.effect != effect:
            raise ExecutableError("unsupported_operation")
        if effect == "write" and operation.reconciliation.mode != "read_back":
            raise ExecutableError("unsupported_operation")
        return operation, document, declaration.ref

    def infer(self, mapping, env):
        try:
            return infer_mapping(mapping, env.types() if type(env) is _Scope else env)
        except ExecutableError as error:
            if error.code == "unknown_reference":
                raise ExecutableError("unavailable_value") from None
            raise

    def assign(self, mapping, schema, env, definitions=()):
        actual = self.infer(mapping, env)
        assert_assignable(actual, _data(schema, definitions))
        return actual

    def handle(self, mapping, kind, env):
        actual = self.infer(mapping, env)
        assert_assignable(actual, parse_export({"kind": "handle", "handle_kind": kind}))
        return actual

    def rules(self, rules, readback, definitions, env, present=frozenset()):
        resolved = resolve_schema(readback, definitions)

        def presence(items):
            found = set()
            for item in items:
                if item.op == "present":
                    found.add(ReferenceKey("package_input", None, item.readback_pointer))
                elif item.op == "and":
                    found.update(presence(item.rules))
            return found

        guarded = present | presence(rules)
        read_env = TypeEnvironment(resolved, present_refs=frozenset(guarded))
        for rule in rules:
            if rule.op in ("and", "or"):
                if rule.op == "and":
                    self.rules(rule.rules, readback, definitions, env, guarded)
                else:
                    for alternative in rule.rules:
                        self.rules([alternative], readback, definitions, env, present)
                continue
            selected = infer_mapping(
                {
                    "kind": "ref",
                    "source": {"kind": "package_input"},
                    "pointer": rule.readback_pointer,
                },
                read_env,
            )
            if rule.op == "present":
                continue
            # Comparisons cannot treat an absent property as a present value.
            if selected.may_be_missing:
                raise ExecutableError("unavailable_value")
            expected = self.infer(rule.expected, env)
            assert_assignable(expected, _data(selected.schema))
            infer_mapping(
                {
                    "kind": "call",
                    "fn": rule.op,
                    "args": [
                        {"kind": "ref", "source": {"kind": "package_input"}, "pointer": ""},
                        {"kind": "ref", "source": {"kind": "package_input"}, "pointer": ""},
                    ],
                },
                TypeEnvironment(selected.schema),
            )

    def check_operation(self, package, spec, env, effect, name=None):
        op, doc, pin = self.operation(package, spec.connector_ref, name or spec.operation, effect)
        self.assign(spec.target, op.target_schema, env, doc.schemas)
        self.assign(spec.input, op.input_schema, env, doc.schemas)
        return op, doc, pin

    def verification(self, package, spec, env):
        op, doc, pin = self.check_operation(package, spec, env, "read", spec.read_operation)
        self.rules(spec.rules, op.output_schema, doc.schemas, env)
        return pin

    def envelope(self, parent, child):
        for name in ("required_capabilities", "destination_classes", "operation_classes"):
            if not set(getattr(child.environment, name)) <= set(getattr(parent.environment, name)):
                raise ExecutableError("invalid_requirements")
        if not set(child.secret_refs) <= set(parent.secret_refs):
            raise ExecutableError("invalid_requirements")
        parent_platforms = {(x.os, x.architecture) for x in parent.environment.platforms}
        if not {(x.os, x.architecture) for x in child.environment.platforms} <= parent_platforms:
            raise ExecutableError("invalid_requirements")
        for name in type(parent.limits).model_fields:
            if getattr(child.limits, name) > getattr(parent.limits, name):
                raise ExecutableError("expansion_exceeded")

    def package(self, package):
        key = (package.package_id, package.version)
        if key in self.counts:
            return self.counts[key]
        digest = self.closure.package_digests[key]
        for declaration in package.subworkflows:
            child = self.closure.packages[(declaration.ref.name, declaration.ref.version)]
            self.envelope(package, child)
            self.package(child)
        self.pending = {}
        self.verified = {}
        env = _Scope(
            package.input_schema,
            schema_definitions=package.schemas,
            input_identity=("input", digest),
        )
        result, count = self.body(package, package.body, "/body", env, (digest,), (), digest)
        assert_assignable(result, _data(package.output_schema, package.schemas))
        if count > package.limits.max_steps or count > 10000:
            raise ExecutableError("expansion_exceeded")
        for source, conditions in self.pending.items():
            verifications = self.verified.get(source, [])
            if not _covered(conditions, [guard for guard, _ in verifications]):
                raise ExecutableError("verification_missing")
            self.obligations.append(
                {
                    "package_sha256": digest,
                    "dispatch": origin_projection(source),
                    "verified_by": [
                        origin_projection(x) for x in dict.fromkeys(v for _, v in verifications)
                    ],
                }
            )
        self.counts[key] = count
        return count

    def body(self, package, body, pointer, env, occurrence, guards, digest, region_id="body"):
        env = replace(
            env, step_outputs=dict(env.step_outputs), value_outputs=dict(env.value_outputs)
        )
        count, previous = 1, pointer
        region_index = len(self.regions)
        self.regions.append(None)
        for index, step in enumerate(body.steps):
            path = f"{pointer}/steps/{index}"
            self.edges.append(
                {
                    "package_sha256": digest,
                    "source_pointer": previous,
                    "target_pointer": path,
                    "kind": "sequence",
                    "target_package_sha256": digest,
                }
            )
            step_index = len(self.steps)
            self.steps.append(None)
            output, attempts, pins, operation = self.step(
                package, step, path, env, occurrence, guards, digest
            )
            count += attempts
            if count > 10000:
                raise ExecutableError("expansion_exceeded")
            output_json = flow_projection(output)
            raw = _json(step)
            mappings = {
                key: raw[key]
                for key in (
                    "mapping",
                    "input",
                    "target",
                    "condition",
                    "intent",
                    "decision",
                    "receipt",
                    "correlation",
                )
                if key in raw
            }
            self.steps[step_index] = {
                "package_sha256": digest,
                "step_id": step.id,
                "region_id": region_id,
                "region_pointer": pointer,
                "source_pointer": path,
                "kind": step.kind,
                "source_sha256": _digest(raw),
                "input_mapping_sha256": _digest(mappings),
                "output_type_sha256": _digest(output_json),
                "output_type": output_json,
                "pins": [_json(x) for x in pins],
                "operation": operation,
                "maximum_step_attempts": attempts,
            }
            env.step_outputs[step.id] = output
            env.value_outputs[step.id] = self.values[(digest, path)]
            previous = path
        result = self.infer(body.result, env)
        self.values[(digest, pointer)] = self.value(body.result, env)
        assert_assignable(result, parse_export(body.result_schema, package.schemas))
        self.regions[region_index] = {
            "package_sha256": digest,
            "source_pointer": pointer,
            "region_id": region_id,
            "kind": "sequence",
            "step_ids": [x.id for x in body.steps],
            "result_mapping_sha256": _digest(_json(body.result)),
            "output_type": flow_projection(result),
            "maximum_step_attempts": count,
        }
        return result, count

    def step(self, package, step, path, env, occurrence, guards, digest):
        kind, pins, operation, count = step.kind, [], None, 1
        null = DataType(resolve_schema({"kind": "null"}))

        def source(op):
            return SourceSymbol(op, step.id, occurrence)

        value = ("step", digest, path, occurrence)

        def nested(
            body,
            suffix,
            scope=env,
            positions=occurrence,
            conditions=guards,
            edge="branch",
            region=None,
        ):
            self.edges.append(
                {
                    "package_sha256": digest,
                    "source_pointer": path,
                    "target_pointer": path + suffix,
                    "kind": edge,
                    "target_package_sha256": digest,
                }
            )
            return self.body(
                package,
                body,
                path + suffix,
                scope,
                positions,
                conditions,
                digest,
                region or f"{kind}:{step.id}:{edge if edge in ('then', 'else') else 'body'}",
            )

        if kind == "transform":
            output = self.assign(step.mapping, step.output_schema, env, package.schemas)
            value = self.value(step.mapping, env)
        elif kind == "decision":
            self.assign(step.condition, {"kind": "boolean"}, env)
            selector_path = (digest, path, *occurrence[1:])
            selector = _selector(selector_path)
            self.selector_templates[(digest, path)] = selector
            left, a = nested(
                step.then_body,
                "/then_body",
                replace(env, present_refs=env.present_refs | presence_guard(step.condition)),
                conditions=(*guards, (selector, 0)),
                edge="then",
            )
            right, b = nested(
                step.else_body, "/else_body", conditions=(*guards, (selector, 1)), edge="else"
            )
            output = merge_flow_elements(
                (left, right),
                parse_export(step.then_body.result_schema, package.schemas),
                selector_path,
            )
            count = 1 + max(a, b)
            value = _choice(
                selector,
                (
                    self.values[(digest, path + "/then_body")],
                    self.values[(digest, path + "/else_body")],
                ),
            )
        elif kind == "parallel":
            values, identities = {}, []
            for i, branch in enumerate(step.branches):
                value, attempts = nested(
                    branch.body, f"/branches/{i}/body", region=f"parallel:{step.id}:{branch.id}"
                )
                values[branch.id] = value
                identities.append((branch.id, self.values[(digest, path + f"/branches/{i}/body")]))
                count += attempts
            output = FlowObjectType(values)
            value = ("object", tuple(identities))
        elif kind == "iteration":
            array = self.infer(step.input, env)
            if array.may_be_missing:
                raise ExecutableError("unavailable_value")
            maximum = (
                array.max_items
                if type(array) is FlowArrayType
                else getattr(array.schema, "max_items", None)
            )
            if maximum is None or maximum > step.max_items:
                raise ExecutableError("type_mismatch")
            owner = "l" + _digest([digest, path])[:63]
            slot = f"$item:{owner}"
            positions = (*occurrence, slot)
            if maximum == 0 and type(array) is DataType:
                item = DataType(array.schema.items)
            elif (
                maximum == 0 and array.item_schema is not None and array.item_schema.kind == "data"
            ):
                # A declared data item type needs no fabricated handle origin, even
                # when a constructor contains no actual elements.
                item = DataType(array.item_schema.schema)
            else:
                item = iteration_item_flow(array, occurrence_path=(slot,))
            array_value = self.value(step.input, env)
            guaranteed = (
                step.input.kind == "array"
                and bool(step.input.items)
                or step.input.kind == "literal"
                and bool(step.input.value)
            )
            loop_conditions = (
                ()
                if guaranteed
                else self.loop_guards.get(
                    getattr(array, "iteration_id", None), (((digest, path, "nonempty"), 1),)
                )
            )
            self.loop_guards[owner] = loop_conditions
            result, attempts = nested(
                step.body,
                "/body",
                replace(env, iteration_item=item, item_identity=_item(array_value, slot)),
                positions=positions,
                conditions=(*guards, *loop_conditions),
                edge="iteration",
            )
            output = FlowArrayType(
                result,
                step.max_items,
                item_schema=parse_export(step.body.result_schema, package.schemas),
                iteration_id=owner,
            )
            count = 1 + step.max_items * attempts
            value = ("repeat", owner, self.values[(digest, path + "/body")])
        elif kind == "connector_read":
            op, doc, pin = self.check_operation(package, step, env, "read")
            output = DataType(resolve_schema(op.output_schema, doc.schemas))
            pins, operation, count = [pin], step.operation, step.retry.max_attempts
        elif kind == "action_intent":
            _, _, pin = self.check_operation(package, step, env, "write")
            policy_ref = self.declaration(package, "policies", step.policy_ref).ref
            policy = self.document(policy_ref).definition
            if (
                policy.approval != step.approval
                or policy.independent_review != step.independent_review
            ):
                raise ExecutableError("intent_mismatch")
            output = HandleType("action_intent", source("action_intent"))
            self.intents[(digest, step.id)] = step
            self.targets[(digest, step.id)] = self.value(step.target, env)
            pins, operation = [pin, policy_ref], step.operation
        elif kind == "human_task":
            pins = [self.declaration(package, "policies", step.assignment_policy_ref).ref]
            if step.escalation_policy_ref:
                pins.append(self.declaration(package, "policies", step.escalation_policy_ref).ref)
            if step.mode == "review":
                intent = self.handle(step.intent, "action_intent", env)
                for leaf in _leaves(intent.source):
                    authored = self.intents[(digest, leaf.step_id)]
                    if (
                        self.declaration(package, "policies", authored.policy_ref).ref
                        != self.declaration(package, "policies", step.assignment_policy_ref).ref
                    ):
                        raise ExecutableError("intent_mismatch")
                output = HandleType(
                    "human_decision", source("human_review"), intent_source=intent.source
                )
            else:
                output = DataType(resolve_schema(step.form_schema, package.schemas))
        elif kind == "action_dispatch":
            intent = self.handle(step.intent, "action_intent", env)
            needs_review = any(
                self.intents[(digest, x.step_id)].approval == "human"
                for x in _leaves(intent.source)
            )
            if step.decision is not None:
                decision = self.handle(step.decision, "human_decision", env)
                if decision.intent_source != intent.source:
                    raise ExecutableError("intent_mismatch")
            elif needs_review:
                raise ExecutableError("intent_mismatch")
            intent_steps = [self.intents[(digest, x.step_id)] for x in _leaves(intent.source)]
            referenced = [
                self.declaration(package, "connectors", x.connector_ref).ref for x in intent_steps
            ]
            referenced.extend(
                self.declaration(package, "policies", x.policy_ref).ref for x in intent_steps
            )
            pins = list({(x.kind, x.name, x.version, x.sha256): x for x in referenced}.values())
            operation_names = {x.operation for x in intent_steps}
            operation = next(iter(operation_names)) if len(operation_names) == 1 else None
            receipt_source = source("action_dispatch")
            output = HandleType("action_receipt", receipt_source, intent_source=intent.source)
            self.pending[receipt_source] = guards
            count = step.retry.max_attempts
            if step.compensation:
                compensation = step.compensation
                policy = self.document(
                    self.declaration(package, "policies", compensation.policy_ref).ref
                ).definition
                if (
                    policy.approval != compensation.approval
                    or policy.independent_review != compensation.independent_review
                ):
                    raise ExecutableError("intent_mismatch")
                self.verification_binding(package, compensation, compensation.verification)
                if _symbol_fingerprint(self.value(compensation.target, env)) != _symbol_fingerprint(
                    self.value(compensation.verification.target, env)
                ):
                    raise ExecutableError("intent_mismatch")
                self.obligations.append(
                    {
                        "package_sha256": digest,
                        "dispatch": origin_projection(
                            SourceSymbol("action_dispatch", step.id, (*occurrence, "compensation"))
                        ),
                        "verified_by": [
                            origin_projection(
                                SourceSymbol("verification", step.id, (*occurrence, "compensation"))
                            )
                        ],
                    }
                )
                _, _, pin = self.check_operation(package, compensation, env, "write")
                pins.extend(
                    [
                        pin,
                        self.declaration(package, "policies", compensation.policy_ref).ref,
                        self.verification(package, compensation.verification, env),
                    ]
                )
                count += (
                    1 + (compensation.approval == "human") + compensation.retry.max_attempts + 1
                )
        elif kind == "outcome_verification":
            receipt = self.handle(step.receipt, "action_receipt", env)
            pins = [self.verification(package, step, env)]
            actual_target = self.value(step.target, env)
            for intent_origin, selections in _origin_options(receipt.intent_source):
                intent_step = self.intents[(digest, intent_origin.step_id)]
                expected = self.targets[(digest, intent_origin.step_id)]
                for owner, binding in intent_origin.occurrence_bindings:
                    expected = _bind(expected, owner, binding)
                contexts = dict(selections)
                if _symbol_fingerprint(_restrict(actual_target, contexts)) != _symbol_fingerprint(
                    _restrict(expected, contexts)
                ):
                    raise ExecutableError("intent_mismatch")
                self.verification_binding(package, intent_step, step)
            verification = source("verification")
            output = HandleType(
                "verification_evidence", verification, intent_source=receipt.intent_source
            )
            for leaf, selection_guards in _selected_leaves(receipt.source):
                for pending in self.pending:
                    if _same_occurrence(pending, leaf, occurrence):
                        # Iterating a complete exported producer array preserves its cardinality,
                        # including a branch-specific array alternative. For this pending
                        # occurrence, the consuming iteration is necessarily nonempty.
                        implied = set()
                        bindings = dict(leaf.occurrence_bindings)
                        for segment in pending.occurrence_path:
                            if not segment.startswith("$item:"):
                                continue
                            for bound in bindings.get(segment[6:], ()):
                                if bound.startswith("$item:"):
                                    implied.update(self.loop_guards.get(bound[6:], ()))
                        coverage = tuple(guard for guard in guards if guard not in implied)
                        correlated = []
                        for selector, option in (*coverage, *selection_guards):
                            # Rebase only the exact specialization of this producing
                            # decision. A different item/owner cannot become its guard.
                            if selector and selector[0] == "selector":
                                template = self.selector_templates.get(selector[1][:2])
                                specialized = template
                                if template is not None:
                                    for owner, binding in leaf.occurrence_bindings:
                                        specialized = _bind_selector(specialized, owner, binding)
                                    if selector == specialized:
                                        selector = template
                            correlated.append((selector, option))
                        self.verified.setdefault(pending, []).append(
                            (tuple(correlated), verification)
                        )
        elif kind == "agent":
            family, local = (
                ("models", step.model_ref) if step.model_ref else ("agents", step.agent_ref)
            )
            pin = self.declaration(package, family, local).ref
            doc = self.document(pin)
            definition = doc.definition
            for name in ("max_input_tokens", "max_output_tokens", "max_cost_microusd"):
                if getattr(step.limits, name) > getattr(package.limits, name):
                    raise ExecutableError("expansion_exceeded")
            if step.limits.timeout_seconds > package.limits.active_seconds:
                raise ExecutableError("expansion_exceeded")
            self.assign(step.input, definition.input_schema, env, doc.schemas)
            assert_assignable(
                DataType(resolve_schema(definition.output_schema, doc.schemas)),
                _data(step.output_schema, package.schemas),
            )
            for name in type(step.limits).model_fields:
                if getattr(step.limits, name) > getattr(definition.limits, name):
                    raise ExecutableError("expansion_exceeded")
            allowed_tools = {
                (x.connector_ref.sha256, x.operation): x for x in definition.read_tools
            }
            for tool in step.read_tools:
                _, _, tool_pin = self.operation(package, tool.connector_ref, tool.operation, "read")
                if (tool_pin.sha256, tool.operation) not in allowed_tools:
                    raise ExecutableError("unsupported_operation")
                allowed = allowed_tools[(tool_pin.sha256, tool.operation)]
                if (
                    tool.retry.max_attempts > allowed.retry.max_attempts
                    or tool.retry.timeout_seconds > allowed.retry.timeout_seconds
                ):
                    raise ExecutableError("expansion_exceeded")
                pins.append(tool_pin)
            for name in step.knowledge_refs:
                declaration = self.declaration(package, "knowledge", name)
                knowledge = self.document(declaration.ref)
                if (
                    knowledge.definition.freshness_seconds > declaration.required_freshness_seconds
                    or knowledge.definition.classification
                    not in declaration.allowed_classifications
                ):
                    raise ExecutableError("invalid_requirements")
                assert_assignable(
                    DataType(
                        resolve_schema(knowledge.definition.citation_schema, knowledge.schemas)
                    ),
                    _data(declaration.citation_schema, package.schemas),
                )
                pins.append(declaration.ref)
            pins.insert(0, pin)
            output = DataType(resolve_schema(step.output_schema, package.schemas))
            count = step.limits.max_steps * step.retry.max_attempts
        elif kind == "timer":
            output = null
        elif kind == "external_wait":
            correlation = self.infer(step.correlation, env)
            if type(correlation) is not DataType or correlation.may_be_missing:
                raise ExecutableError("type_mismatch")
            output = DataType(resolve_schema(step.signal_schema, package.schemas))
            pins = [self.declaration(package, "policies", step.assignment_policy_ref).ref]
            if step.escalation_policy_ref:
                pins.append(self.declaration(package, "policies", step.escalation_policy_ref).ref)
        elif kind == "subworkflow":
            pin = self.declaration(package, "subworkflows", step.workflow_ref).ref
            child = self.closure.packages[(pin.name, pin.version)]
            self.assign(step.input, child.input_schema, env, child.schemas)
            output = DataType(resolve_schema(child.output_schema, child.schemas))
            count = 1 + self.counts[(pin.name, pin.version)]
            pins = [pin]
            self.edges.append(
                {
                    "package_sha256": digest,
                    "source_pointer": path,
                    "target_pointer": "/body",
                    "kind": "call",
                    "target_package_sha256": pin.sha256,
                }
            )
        else:
            raise ExecutableError("unsupported_operation")
        self.values[(digest, path)] = value
        return output, count, pins, operation


def _selected_leaves(origin, guards=()):
    if type(origin) is SourceSymbol:
        return [(origin, guards)]
    selected = []
    for index, option in enumerate(origin.options):
        if option is not None:
            # Decision selectors retain occurrence slots and all ordered selection bindings.
            extra = (
                ((_selector(origin.selection_path, origin.selection_bindings), index),)
                if len(origin.selection_path) >= 2 and origin.selection_path[1].startswith("/body")
                else ()
            )
            selected.extend(_selected_leaves(option, (*guards, *extra)))
    return selected


def _same_occurrence(pending, selected, current):
    if pending == selected:
        return True
    if (pending.operation, pending.step_id) != (selected.operation, selected.step_id):
        return False
    bindings = dict(selected.occurrence_bindings)
    path = []
    for segment in pending.occurrence_path:
        if segment.startswith("$item:") and segment[6:] in bindings:
            bound = bindings[segment[6:]]
            # Only a complete current iteration covers all occurrences. Literal indexing does not.
            if (
                not bound
                or not all(x in current for x in bound)
                or not bound[-1].startswith("$item:")
            ):
                return False
            path.extend(bound)
        else:
            path.append(segment)
    return tuple(path) == selected.occurrence_path


def _covered(required, covers):
    """Bounded exact Boolean coverage over structured decision paths."""
    fixed = dict(required)
    cubes = []
    for cover in covers:
        cube = {}
        valid = True
        for key, value in cover:
            if key in cube and cube[key] != value or key in fixed and fixed[key] != value:
                valid = False
                break
            if key not in fixed:
                cube[key] = value
        if valid:
            cubes.append(frozenset(cube.items()))
    budget = [10000]

    def tautology(items):
        budget[0] -= 1
        if budget[0] < 0:
            raise ExecutableError("expansion_exceeded")
        if frozenset() in items:
            return True
        if not items:
            return False
        key = min((key for cube in items for key, _ in cube), key=repr)
        return all(
            tautology(
                frozenset(
                    frozenset((k, v) for k, v in cube if k != key)
                    for cube in items
                    if (key, 1 - value) not in cube
                )
            )
            for value in (0, 1)
        )

    return tautology(frozenset(cubes))


def compile_package(raw: object, *, dependencies: list[object], catalog: object) -> CompiledPlan:
    closure = resolve_dependencies(raw, dependencies, catalog)
    compiler = _Compiler(closure)
    compiler.check_catalog()
    root = closure.root
    maximum = compiler.package(root)
    root_key = (root.package_id, root.version)

    def identity(kind, key, digest):
        return {"kind": kind, "name": key[0], "version": key[1], "sha256": digest}

    package_order = {digest: index for index, digest in enumerate(closure.package_digests.values())}
    # Package collections are sets; each package's step/region/edge order stays authored.
    for entries in (compiler.steps, compiler.regions, compiler.edges):
        entries.sort(key=lambda entry: package_order[entry["package_sha256"]])
    compiler.obligations.sort(
        key=lambda entry: (
            package_order[entry["package_sha256"]],
            entry["dispatch"]["step_id"],
            entry["dispatch"]["occurrence_path"],
        )
    )
    plan = CompiledPlan.model_validate(
        {
            "package_sha256": closure.package_digests[root_key],
            "dependency_digests": [
                identity("workflow", key, digest)
                for key, digest in closure.package_digests.items()
                if key != root_key
            ],
            "catalog_digests": [
                identity(key[0], key[1:], digest) for key, digest in closure.catalog_digests.items()
            ],
            "steps": compiler.steps,
            "regions": compiler.regions,
            "edges": compiler.edges,
            "requirements": [
                {
                    "package_sha256": closure.package_digests[key],
                    "environment": _json(package.environment),
                    "secret_refs": sorted(package.secret_refs),
                }
                for key, package in closure.packages.items()
            ],
            "limits": _json(root.limits),
            "maximum_step_attempts": maximum,
            "outcome_obligations": compiler.obligations,
        }
    )
    canonical_plan_json(plan)
    return plan
