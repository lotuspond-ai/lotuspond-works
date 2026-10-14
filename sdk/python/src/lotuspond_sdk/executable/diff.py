"""Private, deterministic authored-source differences; no behavioral assurances."""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import Field, JsonValue

from ._canonical import jcs_bytes, sha256_hex
from ._validation import StrictModel, report_bytes, validate_model
from .errors import ExecutableError
from .input import REPORT_LIMITS, project_json
from .package import PACKAGE_MODELS, Digest
from .validation import validate_executable_package

Category = Literal[
    "metadata",
    "data_mapping",
    "control_flow",
    "model_tools",
    "connector_action",
    "approval_policy",
    "outcome",
    "limits",
]
Operation = Literal["added", "removed", "changed", "moved", "reordered"]


class ChangeValues(StrictModel):
    """Explicit local detail; callers must treat both values as private data."""

    before: JsonValue
    after: JsonValue


class ExecutableChange(StrictModel):
    category: Category
    operation: Operation
    identity: str = Field(min_length=1, max_length=160)
    before_path: str | None = Field(max_length=2048)
    after_path: str | None = Field(max_length=2048)
    values: ChangeValues | None = None


class ExecutableDiff(StrictModel):
    before_sha256: Digest
    after_sha256: Digest
    changes: list[ExecutableChange] = Field(max_length=10000)
    include_values: bool = False
    authority_granted: Literal[False] = False
    behavioral_compatibility: Literal["not_assessed"] = "not_assessed"

    def model_dump_json(self, **kwargs) -> str:
        """Complete bounded ASCII output; use the byte API for CLI output."""
        if kwargs:
            raise TypeError("Executable diffs use complete fixed-format serialization.")
        return executable_diff_json(self).decode("ascii").removesuffix("\n")

    def model_dump(self, **kwargs) -> dict:
        if kwargs:
            raise TypeError("Executable diffs use complete fixed-format serialization.")
        return json.loads(executable_diff_json(self))


DIFF_MODELS = (ChangeValues, ExecutableChange, ExecutableDiff)
_FAMILIES = {
    "models": "model_tools",
    "agents": "model_tools",
    "knowledge": "model_tools",
    "connectors": "connector_action",
    "artifacts": "metadata",
    "subworkflows": "control_flow",
    "evaluations": "outcome",
    "policies": "approval_policy",
    "schemas": "data_mapping",
    "secret_refs": "connector_action",
}
_ID = r"[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*"
_IDENTITY = re.compile(
    rf"(?:package|body|step:{_ID}|decision:{_ID}:(?:then|else)|"
    rf"parallel:{_ID}:{_ID}|iteration:{_ID}:body|"
    rf"(?:{'|'.join(_FAMILIES)})(?::{_ID})?)\Z"
)
# These are SDK-owned field names only. Data keys/pointers never enter paths.
_TOKENS = {name for model in PACKAGE_MODELS for name in model.model_fields}
_STRUCTURED = {"limits", "retry", "ref", "environment", "assurance", "compensation", "verification"}
_DATA = {
    "input",
    "target",
    "mapping",
    "input_schema",
    "output_schema",
    "schema",
    "citation_schema",
    "form_schema",
    "signal_schema",
    "result",
    "result_schema",
    "correlation",
}
_APPROVAL = {
    "policy_ref",
    "approval",
    "independent_review",
    "assignment_policy_ref",
    "escalation_policy_ref",
    "expiry_seconds",
    "mode",
    "form_schema",
}
_LIMITS = {
    "limits",
    "retry",
    "delay_seconds",
    "timeout_seconds",
    "max_items",
    "concurrency",
}
_TOOLS = {"instructions", "model_ref", "agent_ref", "read_tools", "knowledge_refs"}


def _key(change):
    return (
        change.category,
        change.identity,
        change.before_path or "",
        change.after_path or "",
        change.operation,
    )


def _path_valid(path):
    if type(path) is not str or not path.startswith("/"):
        return False
    return all(
        token in _TOKENS or re.fullmatch(r"0|[1-9][0-9]{0,3}", token)
        for token in path[1:].split("/")
    )


def validate_executable_diff(raw: object) -> ExecutableDiff:
    """Admit the complete normalized report, including privacy and ASCII bounds.

    This validates report structure, never authenticates its source hashes or
    claims. CLI wrappers must run report_bytes on the complete wrapper with
    DIFF_MODELS included in its explicit model allowlist.
    """
    report = validate_model(
        raw,
        model_type=ExecutableDiff,
        model_types=DIFF_MODELS,
        code="invalid_package",
        limits=REPORT_LIMITS,
    )
    seen = set()
    for change in report.changes:
        before, after = change.before_path, change.after_path
        if (
            not _IDENTITY.fullmatch(change.identity)
            or (before is not None and not _path_valid(before))
            or (after is not None and not _path_valid(after))
            or (change.operation == "added" and (before is not None or after is None))
            or (change.operation == "removed" and (before is None or after is not None))
            or (
                change.operation in ("changed", "moved", "reordered")
                and (before is None or after is None)
            )
            or (
                change.operation == "moved"
                and (
                    change.category != "control_flow"
                    or not change.identity.startswith("step:")
                    or before == after
                )
            )
            or (change.values is not None and not report.include_values)
            or (report.include_values and change.values is None)
            or (
                change.values is not None
                and (
                    (change.operation == "added" and change.values.before is not None)
                    or (change.operation == "removed" and change.values.after is not None)
                )
            )
            or _key(change) in seen
        ):
            raise ExecutableError("invalid_package")
        seen.add(_key(change))
    if report.changes != sorted(report.changes, key=_key):
        raise ExecutableError("invalid_package")
    report_bytes(report, model_type=ExecutableDiff, model_types=DIFF_MODELS, code="invalid_package")
    return report


def executable_diff_json(raw: object) -> bytes:
    """Revalidate and emit complete ASCII JSON plus newline, capped at 8 MiB."""
    return report_bytes(
        validate_executable_diff(raw),
        model_type=ExecutableDiff,
        model_types=DIFF_MODELS,
        code="invalid_package",
    )


def _pointer(parent, token):
    return parent + "/" + str(token).replace("~", "~0").replace("/", "~1")


def _local_step(step):
    """Parent payload without independently tracked child regions/order."""
    return {
        key: value
        for key, value in step.items()
        if key not in ("then_body", "else_body", "body", "branches")
    }


def _local_body(body):
    return {key: value for key, value in body.items() if key != "steps"}


def _index(root):
    steps, regions = {}, {}

    def visit(body, identity, path):
        regions[identity] = (path, body)
        for index, step in enumerate(body["steps"]):
            step_path = _pointer(_pointer(path, "steps"), index)
            steps[step["id"]] = (step_path, identity, step)
            if step["kind"] == "decision":
                for field, suffix in (("then_body", "then"), ("else_body", "else")):
                    visit(
                        step[field], f"decision:{step['id']}:{suffix}", _pointer(step_path, field)
                    )
            elif step["kind"] == "iteration":
                visit(step["body"], f"iteration:{step['id']}:body", _pointer(step_path, "body"))
            elif step["kind"] == "parallel":
                for index, branch in enumerate(step["branches"]):
                    branch_path = _pointer(_pointer(step_path, "branches"), index)
                    visit(
                        branch["body"],
                        f"parallel:{step['id']}:{branch['id']}",
                        _pointer(branch_path, "body"),
                    )

    visit(root["body"], "body", "/body")
    return steps, regions


def _category(field, fallback, kind=None):
    if field in ("assurance", "verification"):
        return "outcome"
    if field in _LIMITS:
        return "limits"
    if field in _TOOLS:
        return "model_tools"
    if field in _APPROVAL:
        return "approval_policy"
    if kind == "outcome_verification":
        return "outcome"
    if field in _DATA:
        return "data_mapping"
    if field in (
        "connector_ref",
        "operation",
        "intent",
        "decision",
        "compensation",
        "credential_ref",
    ):
        return "approval_policy" if kind == "human_task" else "connector_action"
    return fallback


def diff_executable_packages(
    before: object, after: object, *, include_values: bool = False
) -> ExecutableDiff:
    """Compare structurally admitted source; no dependency resolution or execution.

    Match global step IDs, stable owner-scoped regions, and family-local declaration
    aliases (schemas by name, secret references by value). Public arrays remain
    significant; common-item order excludes additions/removals and region moves.
    """
    if type(include_values) is not bool:
        raise TypeError("include_values must be a boolean.")
    left, right = [
        project_json(validate_executable_package(raw), model_types=PACKAGE_MODELS)
        for raw in (before, after)
    ]
    changes = []

    def emit(category, operation, identity, old_path, new_path, old, new):
        changes.append(
            ExecutableChange(
                category=category,
                operation=operation,
                identity=identity,
                before_path=old_path,
                after_path=new_path,
                values=ChangeValues(before=old, after=new) if include_values else None,
            )
        )

    def fields(old, new, old_path, new_path, category, identity, kind=None):
        for field in sorted(old.keys() | new.keys()):
            if field in ("id", "kind") and old.get(field) == new.get(field):
                continue
            old_field, new_field = old.get(field), new.get(field)
            if old_field == new_field and (field in old) == (field in new):
                continue
            cat = _category(field, category, kind)
            opath = _pointer(old_path, field) if field in old else None
            npath = _pointer(new_path, field) if field in new else None
            if field in _STRUCTURED and type(old_field) is dict and type(new_field) is dict:
                fields(old_field, new_field, opath, npath, cat, identity)
            else:
                emit(
                    cat,
                    "changed"
                    if field in old and field in new
                    else "added"
                    if field in new
                    else "removed",
                    identity,
                    opath,
                    npath,
                    old_field,
                    new_field,
                )

    def order(old, new, old_path, new_path, category, identity):
        common = set(old) & set(new)
        old_common = [key for key in old if key in common]
        new_common = [key for key in new if key in common]
        if old_common != new_common:
            emit(category, "reordered", identity, old_path, new_path, old_common, new_common)

    def declarations(family, category):
        old, new = left[family], right[family]
        key = (
            (lambda entry: entry)
            if family == "secret_refs"
            else (lambda entry: entry["name" if family == "schemas" else "id"])
        )
        oi = {key(entry): index for index, entry in enumerate(old)}
        ni = {key(entry): index for index, entry in enumerate(new)}
        path = _pointer("", family)
        order(list(oi), list(ni), path, path, category, family)
        for identifier in sorted(oi.keys() | ni.keys()):
            identity = f"{family}:{identifier}"
            opath = _pointer(path, oi[identifier]) if identifier in oi else None
            npath = _pointer(path, ni[identifier]) if identifier in ni else None
            old_value = old[oi[identifier]] if opath is not None else None
            new_value = new[ni[identifier]] if npath is not None else None
            if opath is None or npath is None:
                emit(
                    category,
                    "added" if opath is None else "removed",
                    identity,
                    opath,
                    npath,
                    old_value,
                    new_value,
                )
            elif family != "secret_refs":
                fields(old_value, new_value, opath, npath, category, identity)

    for family, category in _FAMILIES.items():
        declarations(family, category)
    excluded = {*_FAMILIES, "body"}
    fields(
        {k: v for k, v in left.items() if k not in excluded},
        {k: v for k, v in right.items() if k not in excluded},
        "",
        "",
        "metadata",
        "package",
    )
    old_steps, old_regions = _index(left)
    new_steps, new_regions = _index(right)
    for identity in sorted(old_regions.keys() | new_regions.keys()):
        old, new = old_regions.get(identity), new_regions.get(identity)
        if old is None or new is None:
            emit(
                "control_flow",
                "added" if old is None else "removed",
                identity,
                old[0] if old else None,
                new[0] if new else None,
                _local_body(old[1]) if old else None,
                _local_body(new[1]) if new else None,
            )
        else:
            fields(
                _local_body(old[1]), _local_body(new[1]), old[0], new[0], "data_mapping", identity
            )
            # Common steps that stayed in this region, independent of moves.
            stayed = {
                identifier
                for identifier in old_steps.keys() & new_steps.keys()
                if old_steps[identifier][1] == new_steps[identifier][1] == identity
            }
            order(
                [s["id"] for s in old[1]["steps"] if s["id"] in stayed],
                [s["id"] for s in new[1]["steps"] if s["id"] in stayed],
                _pointer(old[0], "steps"),
                _pointer(new[0], "steps"),
                "control_flow",
                identity,
            )
    for identifier in sorted(old_steps.keys() | new_steps.keys()):
        identity = f"step:{identifier}"
        old, new = old_steps.get(identifier), new_steps.get(identifier)
        if old is None or new is None:
            emit(
                "control_flow",
                "added" if old is None else "removed",
                identity,
                old[0] if old else None,
                new[0] if new else None,
                _local_step(old[2]) if old else None,
                _local_step(new[2]) if new else None,
            )
            continue
        if old[1] != new[1]:
            emit("control_flow", "moved", identity, old[0], new[0], old[1], new[1])
        if old[2]["kind"] != new[2]["kind"]:
            emit(
                "control_flow",
                "changed",
                identity,
                old[0],
                new[0],
                _local_step(old[2]),
                _local_step(new[2]),
            )
        else:
            fields(
                _local_step(old[2]),
                _local_step(new[2]),
                old[0],
                new[0],
                "control_flow",
                identity,
                old[2]["kind"],
            )
            if old[2]["kind"] == "parallel":
                order(
                    [b["id"] for b in old[2]["branches"]],
                    [b["id"] for b in new[2]["branches"]],
                    _pointer(old[0], "branches"),
                    _pointer(new[0], "branches"),
                    "control_flow",
                    identity,
                )
    changes.sort(key=_key)
    return validate_executable_diff(
        ExecutableDiff(
            before_sha256=sha256_hex(jcs_bytes(left)),
            after_sha256=sha256_hex(jcs_bytes(right)),
            changes=changes,
            include_values=include_values,
        )
    )
