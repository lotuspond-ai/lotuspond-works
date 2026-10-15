"""Deterministic structural JSON Schemas; no hash or authority verification."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Annotated, Literal, Union

from pydantic import Field, TypeAdapter

from .errors import ExecutableError
from .package import ExecutablePackage
from .plan import CompiledPlan
from .wire import ACTION_EVENT_KINDS, EVENT_PAYLOAD_MODELS, STEP_EVENT_KINDS, WIRE_RECORD_MODELS

SchemaKind = Literal["package", "plan", "runtime"]
SCHEMA_FILES = {
    "package": "workflow-package.v2.schema.json",
    "plan": "compiled-workflow.v2.schema.json",
    "runtime": "runtime-contracts.v1.schema.json",
}


def export_schema(kind: SchemaKind) -> dict:
    """Export shape contracts; callers still use public semantic validators."""
    if type(kind) is not str or kind not in SCHEMA_FILES:
        raise ExecutableError("invalid_schema")
    if kind == "runtime":
        schema = TypeAdapter(
            Annotated[Union[WIRE_RECORD_MODELS], Field(discriminator="record_type")]
        ).json_schema()
        schema["required"] = ["record_type"]
        event = schema["$defs"]["RuntimeEvent"]
        variants = []
        for event_kind, payload in EVENT_PAYLOAD_MODELS.items():
            step = event_kind in STEP_EVENT_KINDS or event_kind in ACTION_EVENT_KINDS
            action = event_kind in ACTION_EVENT_KINDS
            properties = {
                "event_kind": {"const": event_kind},
                "payload": {"$ref": f"#/$defs/{payload.__name__}"},
            }
            for field, present in (("step_id", step), ("attempt", step), ("action_id", action)):
                properties[field] = {"not": {"type": "null"}} if present else {"type": "null"}
            variants.append(
                {
                    "properties": properties,
                    "required": ["event_kind", "payload", "step_id", "action_id", "attempt"],
                }
            )
        event["allOf"] = [{"oneOf": variants}]
    else:
        schema = (ExecutablePackage if kind == "package" else CompiledPlan).model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$comment"] = (
        "Structural contract only. Use SDK public validators for bounds, semantic references, hashes and correlations. No authentication or authority is established."
    )
    return schema


def schema_bytes(kind: SchemaKind) -> bytes:
    data = (
        json.dumps(
            export_schema(kind), indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False
        )
        + "\n"
    ).encode("ascii")
    if len(data) > 8388608:
        raise ExecutableError("report_too_large")
    return data


def main(argv: list[str] | None = None) -> int:
    """Developer parity command: --check compares bytes without writing."""
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--directory", type=Path, default=Path("contracts"))
    args = parser.parse_args(argv)
    for kind, name in SCHEMA_FILES.items():
        path = args.directory / name
        data = schema_bytes(kind)
        if args.check:
            if not path.is_file() or path.read_bytes() != data:
                print("Executable schema differs. Regenerate and review it.")
                return 2
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
