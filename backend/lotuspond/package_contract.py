"""Bounded declarative workflow packages; validation does not execute or authorize them."""

from __future__ import annotations

import hashlib
import json
import math
from collections import deque
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

MAX_PACKAGE_BYTES = 1024 * 1024
MAX_JSON_DEPTH = 32
MAX_JSON_ITEMS = 10000
SCHEMA_VERSION = "lotuspond.workflow-package.v1"

ERROR_MESSAGES = {
    "input_too_large": "Package input exceeds the byte limit.",
    "input_too_deep": "Package input exceeds the nesting limit.",
    "input_too_complex": "Package input exceeds the item limit.",
    "invalid_json_value": "Package input must contain only JSON values.",
    "invalid_unicode": "Package input contains invalid Unicode.",
    "nonfinite_number": "Package input must not contain nonfinite numbers.",
    "cyclic_input": "Package input must not contain cyclic objects.",
    "duplicate_identifier": "Identifiers must be unique within each declaration list.",
    "duplicate_operation": "Connector operations must be unique.",
    "unknown_model_reference": "An agent references an undeclared model.",
    "unknown_connector_reference": "A node references an undeclared connector.",
    "unknown_secret_reference": "A connector references an undeclared secret reference.",
    "unknown_operation": "A node operation is not declared by its connector.",
    "invalid_entry": "Exactly one start node must match the entry node and have no incoming edges.",
    "missing_terminal": "The package must declare at least one end node.",
    "unknown_edge_node": "Every edge must reference declared nodes.",
    "duplicate_edge": "Edges must be unique.",
    "terminal_outgoing": "End nodes must not have outgoing edges.",
    "nonterminal_dead_end": "Every nonterminal node must have an outgoing edge.",
    "unreachable_node": "Every node must be reachable from the entry node.",
    "graph_cycle": "The package graph must be acyclic.",
}


def _reject(code: str) -> None:
    raise PydanticCustomError(code, ERROR_MESSAGES[code])


def check_json_bounds(raw: object) -> None:
    """Bound programmatic input before recursive model validation or JSON encoding."""
    stack = [(raw, 0, False)]
    active: set[int] = set()
    count = 0
    byte_lower_bound = 0
    while stack:
        value, depth, exiting = stack.pop()
        if exiting:
            active.remove(id(value))
            continue
        count += 1
        if count > MAX_JSON_ITEMS:
            _reject("input_too_complex")
        if depth > MAX_JSON_DEPTH:
            _reject("input_too_deep")
        if type(value) is str:
            if len(value) > MAX_PACKAGE_BYTES:
                _reject("input_too_large")
            try:
                byte_lower_bound += len(value.encode("utf-8"))
            except UnicodeEncodeError:
                _reject("invalid_unicode")
        elif type(value) is float:
            if not math.isfinite(value):
                _reject("nonfinite_number")
        elif value is None or type(value) in {bool, int}:
            if type(value) is int and value.bit_length() > 64:
                _reject("invalid_json_value")
        elif type(value) in {dict, list}:
            required_visits = len(value) * (2 if type(value) is dict else 1)
            if count + required_visits > MAX_JSON_ITEMS:
                _reject("input_too_complex")
            if id(value) in active:
                _reject("cyclic_input")
            active.add(id(value))
            stack.append((value, depth, True))
            if type(value) is dict:
                if any(type(key) is not str for key in value):
                    _reject("invalid_json_value")
                for key, child in value.items():
                    stack.append((key, depth + 1, False))
                    stack.append((child, depth + 1, False))
            else:
                stack.extend((child, depth + 1, False) for child in value)
        else:
            _reject("invalid_json_value")
        if byte_lower_bound > MAX_PACKAGE_BYTES:
            _reject("input_too_large")
    encoded = json.dumps(raw, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode(
        "utf-8"
    )
    if len(encoded) > MAX_PACKAGE_BYTES:
        _reject("input_too_large")


Identifier = Annotated[
    str, Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*$")
]
Version = Annotated[
    str,
    Field(
        max_length=20, pattern=r"^(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})$"
    ),
]
ReferenceRevision = Annotated[
    str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
]


class ContractModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, hide_input_in_errors=True, revalidate_instances="always"
    )


class SecretReference(ContractModel):
    """A configuration name, never a credential value or a secret-store path."""

    id: Identifier


class ModelReference(ContractModel):
    id: Identifier
    profile: Identifier
    revision: ReferenceRevision


class ConnectorReference(ContractModel):
    id: Identifier
    connector: Identifier
    version: Version
    secret_ref: Identifier | None = None
    operations: list[Identifier] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def unique_operations(self) -> ConnectorReference:
        if len(self.operations) != len(set(self.operations)):
            _reject("duplicate_operation")
        return self


class AgentLimits(ContractModel):
    max_steps: int = Field(ge=1, le=100)
    timeout_seconds: int = Field(ge=1, le=3600)
    max_input_tokens: int = Field(ge=1, le=1000000)
    max_output_tokens: int = Field(ge=1, le=100000)
    max_cost_microusd: int = Field(ge=1, le=1000000000)


class StartNode(ContractModel):
    id: Identifier
    kind: Literal["start"]


class EndNode(ContractModel):
    id: Identifier
    kind: Literal["end"]


class AgentNode(ContractModel):
    id: Identifier
    kind: Literal["agent"]
    model_ref: Identifier
    instructions: str = Field(min_length=1, max_length=16384)
    limits: AgentLimits


class ConnectorNode(ContractModel):
    id: Identifier
    kind: Literal["connector"]
    connector_ref: Identifier
    operation: Identifier


class HumanReviewNode(ContractModel):
    id: Identifier
    kind: Literal["human_review"]


Node = Annotated[
    StartNode | EndNode | AgentNode | ConnectorNode | HumanReviewNode, Field(discriminator="kind")
]


class Edge(ContractModel):
    source: Identifier
    target: Identifier


class WorkflowPackage(ContractModel):
    """Version-one topology/reference contract, not an executable or trusted package."""

    model_config = ConfigDict(
        json_schema_extra={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "urn:lotuspond:workflow-package:v1",
        }
    )

    schema_version: Literal["lotuspond.workflow-package.v1"]
    package_id: Identifier
    version: Version
    name: str = Field(min_length=1, max_length=160)
    entry_node: Identifier
    models: list[ModelReference] = Field(max_length=32)
    connectors: list[ConnectorReference] = Field(max_length=32)
    secret_refs: list[SecretReference] = Field(max_length=32)
    nodes: list[Node] = Field(min_length=2, max_length=128)
    edges: list[Edge] = Field(min_length=1, max_length=256)

    @model_validator(mode="wrap")
    @classmethod
    def bounded_input(cls, raw: object, handler) -> WorkflowPackage:
        # A wrap validator sees the instance before Pydantic turns it into a
        # dictionary still containing nested models. Never trust model instances.
        if isinstance(raw, cls):
            raw = _model_input(raw)
        check_json_bounds(raw)
        return handler(raw)

    @model_validator(mode="after")
    def validate_graph(self) -> WorkflowPackage:
        for declarations in (self.models, self.connectors, self.secret_refs, self.nodes):
            ids = [item.id for item in declarations]
            if len(ids) != len(set(ids)):
                _reject("duplicate_identifier")
        models = {item.id for item in self.models}
        secrets = {item.id for item in self.secret_refs}
        connectors = {item.id: item for item in self.connectors}
        nodes = {item.id: item for item in self.nodes}
        for connector in self.connectors:
            if connector.secret_ref is not None and connector.secret_ref not in secrets:
                _reject("unknown_secret_reference")
        for node in self.nodes:
            if isinstance(node, AgentNode) and node.model_ref not in models:
                _reject("unknown_model_reference")
            if isinstance(node, ConnectorNode):
                if node.connector_ref not in connectors:
                    _reject("unknown_connector_reference")
                if node.operation not in connectors[node.connector_ref].operations:
                    _reject("unknown_operation")
        starts = [node.id for node in self.nodes if isinstance(node, StartNode)]
        if starts != [self.entry_node]:
            _reject("invalid_entry")
        if not any(isinstance(node, EndNode) for node in self.nodes):
            _reject("missing_terminal")
        outgoing: dict[str, list[str]] = {identifier: [] for identifier in nodes}
        incoming = dict.fromkeys(nodes, 0)
        seen_edges: set[tuple[str, str]] = set()
        for edge in self.edges:
            if edge.source not in nodes or edge.target not in nodes:
                _reject("unknown_edge_node")
            pair = (edge.source, edge.target)
            if pair in seen_edges:
                _reject("duplicate_edge")
            seen_edges.add(pair)
            outgoing[edge.source].append(edge.target)
            incoming[edge.target] += 1
        if incoming[self.entry_node]:
            _reject("invalid_entry")
        for identifier, node in nodes.items():
            if isinstance(node, EndNode) and outgoing[identifier]:
                _reject("terminal_outgoing")
            if not isinstance(node, EndNode) and not outgoing[identifier]:
                _reject("nonterminal_dead_end")
        reached: set[str] = set()
        pending = [self.entry_node]
        while pending:
            identifier = pending.pop()
            if identifier not in reached:
                reached.add(identifier)
                pending.extend(outgoing[identifier])
        if reached != set(nodes):
            _reject("unreachable_node")
        ready = deque(identifier for identifier, count in incoming.items() if not count)
        visited = 0
        while ready:
            identifier = ready.popleft()
            visited += 1
            for target in outgoing[identifier]:
                incoming[target] -= 1
                if not incoming[target]:
                    ready.append(target)
        if visited != len(nodes):
            _reject("graph_cycle")
        # Defaults are part of canonical content. Admit only packages whose
        # complete normalized document fits, so a successful validation always
        # supports canonicalization/digest without increasing untrusted bounds.
        check_json_bounds(self.model_dump(mode="python", warnings=False))
        return self


def _model_input(package: WorkflowPackage) -> dict:
    """Project known model fields without invoking serializers on mutated values."""
    known = (
        WorkflowPackage,
        ModelReference,
        ConnectorReference,
        SecretReference,
        AgentLimits,
        StartNode,
        EndNode,
        AgentNode,
        ConnectorNode,
        HumanReviewNode,
        Edge,
    )
    active: set[int] = set()
    visits = 0

    def project(value: object, depth: int) -> object:
        nonlocal visits
        visits += 1
        if visits > MAX_JSON_ITEMS:
            _reject("input_too_complex")
        if depth > MAX_JSON_DEPTH:
            _reject("input_too_deep")
        kind = type(value)
        if value is None or kind in (str, bool, int, float):
            return value
        if kind in known:
            data = object.__getattribute__(value, "__dict__")
            extras = object.__getattribute__(value, "__pydantic_extra__")
            if type(data) is not dict or any(type(key) is not str for key in data):
                _reject("invalid_json_value")
            if extras is not None and (type(extras) is not dict or len(extras)):
                raise PydanticCustomError("extra_forbidden", "Extra fields are not permitted.")
            if set(data) - set(kind.model_fields):
                raise PydanticCustomError("extra_forbidden", "Extra fields are not permitted.")
        elif kind is dict:
            data = value
            if any(type(key) is not str for key in data):
                _reject("invalid_json_value")
        elif kind is list:
            data = value
        else:
            _reject("invalid_json_value")
        if len(data) > MAX_JSON_ITEMS:
            _reject("input_too_complex")
        if id(value) in active:
            _reject("cyclic_input")
        active.add(id(value))
        try:
            if kind is list:
                return [project(child, depth + 1) for child in data]
            return {key: project(child, depth + 1) for key, child in data.items()}
        finally:
            active.remove(id(value))

    return project(package, 0)


def validate_package(raw: object) -> WorkflowPackage:
    return WorkflowPackage.model_validate(raw)


def canonical_package_json(package: WorkflowPackage) -> bytes:
    """Validated sorted-key UTF-8 JSON with defaults; array order remains significant."""
    # Revalidate known model fields before a serializer can coerce mutated values
    # or silently discard unknown fields.
    validated = validate_package(package)
    return json.dumps(
        validated.model_dump(mode="json"),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def package_digest(package: WorkflowPackage) -> str:
    """Content identity only: this digest provides neither a signature nor trust."""
    return hashlib.sha256(canonical_package_json(package)).hexdigest()
