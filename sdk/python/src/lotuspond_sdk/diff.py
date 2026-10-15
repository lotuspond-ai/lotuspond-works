"""Deterministic local content differences; no compatibility or authority claims."""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_serializer

from .contract import Identifier, package_digest, validate_package
from .errors import SdkError

MAX_REPORT_BYTES = 8 * 1024 * 1024
Category = Literal[
    "graph", "model", "instructions", "limits", "connector", "secret_reference", "metadata"
]
Operation = Literal["added", "removed", "modified", "reordered"]


class _ReportModel(BaseModel):
    model_config = ConfigDict(
        strict=True, extra="forbid", hide_input_in_errors=True, revalidate_instances="always"
    )

    def model_dump_json(self, **kwargs) -> str:
        """Revalidate mutable report models and enforce ASCII-safe bounded output."""
        validated = type(self).model_validate(self)
        kwargs["ensure_ascii"] = True
        encoded = BaseModel.model_dump_json(validated, **kwargs)
        if len(encoded) > MAX_REPORT_BYTES:
            raise SdkError("report_too_large")
        return encoded

    def model_dump(self, **kwargs) -> dict:
        validated = type(self).model_validate(self)
        # Check complete JSON size even if the caller requests a partial dump.
        encoded = BaseModel.model_dump_json(validated, ensure_ascii=True)
        if len(encoded) > MAX_REPORT_BYTES:
            raise SdkError("report_too_large")
        return BaseModel.model_dump(validated, **kwargs)


class ChangeValues(_ReportModel):
    """Potentially sensitive JSON values, present only for explicit detail mode."""

    before: JsonValue
    after: JsonValue

    @field_validator("before", "after")
    @classmethod
    def finite_json(cls, value):
        pending = [value]
        while pending:
            child = pending.pop()
            if isinstance(child, float) and not math.isfinite(child):
                raise ValueError("Report values must contain only finite JSON numbers.")
            if isinstance(child, dict):
                pending.extend(child.values())
            elif isinstance(child, list):
                pending.extend(child)
        return value


class PackageChange(_ReportModel):
    category: Category
    operation: Operation
    declaration_id: Identifier | None
    before_path: str | None
    after_path: str | None
    values: ChangeValues | None = None

    @model_serializer(mode="wrap")
    def omit_private_values(self, handler):
        serialized = handler(self)
        if self.values is None:
            serialized.pop("values", None)
        return serialized


class PackageDiff(_ReportModel):
    before_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    after_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    changes: tuple[PackageChange, ...]
    authority_granted: Literal[False] = False

    @field_validator("authority_granted", mode="before")
    @classmethod
    def strict_authority(cls, value):
        if value is not False:
            raise ValueError("Differences cannot grant authority.")
        return value


def _pointer(parent: str, token: str | int) -> str:
    return parent + "/" + str(token).replace("~", "~0").replace("/", "~1")


def diff_packages(before: object, after: object, *, include_values: bool = False) -> PackageDiff:
    """Compare validated normalized documents without exposing values by default.

    Declarations match by ID, edges by (source, target), operations by value.
    Reorders describe changed relative order among common elements, independently
    of field edits and additions/removals. Paths refer to each normalized input.
    """
    if type(include_values) is not bool:
        raise TypeError("include_values must be a boolean.")
    before_package = validate_package(before)
    after_package = validate_package(after)
    before_sha256 = package_digest(before_package)
    after_sha256 = package_digest(after_package)
    left = before_package.model_dump(mode="json")
    right = after_package.model_dump(mode="json")
    changes: list[PackageChange] = []

    def emit(category, operation, declaration_id, before_path, after_path, old, new):
        changes.append(
            PackageChange(
                category=category,
                operation=operation,
                declaration_id=declaration_id,
                before_path=before_path,
                after_path=after_path,
                values=ChangeValues(before=old, after=new) if include_values else None,
            )
        )

    def compare_fields(old, new, before_path, after_path, category, declaration_id):
        for field in sorted(old.keys() | new.keys()):
            field_category = {
                "model_ref": "model",
                "connector_ref": "connector",
                "operation": "connector",
                "secret_ref": "secret_reference",
                "instructions": "instructions",
                "limits": "limits",
            }.get(field, category)
            old_path = _pointer(before_path, field) if field in old else None
            new_path = _pointer(after_path, field) if field in new else None
            if field not in old:
                emit(field_category, "added", declaration_id, None, new_path, None, new[field])
            elif field not in new:
                emit(field_category, "removed", declaration_id, old_path, None, old[field], None)
            elif old[field] != new[field]:
                if field == "operations":
                    compare_sequence(
                        old[field],
                        new[field],
                        old_path,
                        new_path,
                        "connector",
                        declaration_id,
                        lambda value: value,
                    )
                elif isinstance(old[field], dict) and isinstance(new[field], dict):
                    compare_fields(
                        old[field], new[field], old_path, new_path, field_category, declaration_id
                    )
                else:
                    emit(
                        field_category,
                        "modified",
                        declaration_id,
                        old_path,
                        new_path,
                        old[field],
                        new[field],
                    )

    def compare_sequence(old, new, before_path, after_path, category, declaration_id, key):
        old_keys = [key(value) for value in old]
        new_keys = [key(value) for value in new]
        old_indices = {value: index for index, value in enumerate(old_keys)}
        new_indices = {value: index for index, value in enumerate(new_keys)}
        common = old_indices.keys() & new_indices.keys()
        if [value for value in old_keys if value in common] != [
            value for value in new_keys if value in common
        ]:
            # Reorder payloads contain identities, avoiding duplicate large declarations.
            old_order = [list(value) if isinstance(value, tuple) else value for value in old_keys]
            new_order = [list(value) if isinstance(value, tuple) else value for value in new_keys]
            emit(
                category, "reordered", declaration_id, before_path, after_path, old_order, new_order
            )
        for identifier in old_indices.keys() | new_indices.keys():
            old_index = old_indices.get(identifier)
            new_index = new_indices.get(identifier)
            old_path = _pointer(before_path, old_index) if old_index is not None else None
            new_path = _pointer(after_path, new_index) if new_index is not None else None
            old_value = old[old_index] if old_index is not None else None
            new_value = new[new_index] if new_index is not None else None
            item_id = (
                (old_value or new_value)["id"]
                if isinstance(old_value or new_value, dict) and "id" in (old_value or new_value)
                else declaration_id
            )
            if old_index is None:
                emit(category, "added", item_id, None, new_path, None, new_value)
            elif new_index is None:
                emit(category, "removed", item_id, old_path, None, old_value, None)
            elif old_value != new_value:
                if category == "graph" and old_value.get("kind") != new_value.get("kind"):
                    # Different node kinds have incompatible field sets: one replacement.
                    emit(category, "modified", item_id, old_path, new_path, old_value, new_value)
                else:
                    compare_fields(old_value, new_value, old_path, new_path, category, item_id)

    if before_sha256 != after_sha256:
        for field in ("package_id", "name", "version", "entry_node"):
            if left[field] != right[field]:
                category = "graph" if field == "entry_node" else "metadata"
                path = _pointer("", field)
                emit(category, "modified", None, path, path, left[field], right[field])
        for collection, category in (
            ("nodes", "graph"),
            ("edges", "graph"),
            ("models", "model"),
            ("connectors", "connector"),
            ("secret_refs", "secret_reference"),
        ):
            key = (
                (lambda edge: (edge["source"], edge["target"]))
                if collection == "edges"
                else (lambda declaration: declaration["id"])
            )
            path = _pointer("", collection)
            compare_sequence(left[collection], right[collection], path, path, category, None, key)
    changes.sort(
        key=lambda change: (
            change.category,
            change.declaration_id or "",
            change.before_path or "",
            change.after_path or "",
            change.operation,
        )
    )
    report = PackageDiff(
        before_sha256=before_sha256, after_sha256=after_sha256, changes=tuple(changes)
    )
    report.model_dump_json()  # Fail closed on oversized reports at the public boundary.
    return report
