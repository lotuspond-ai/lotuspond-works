"""Separate structural activity schema; semantic admission remains required."""

from .models import ActivityEnvelope


def activity_schema() -> dict:
    """Return a fresh Draft 2020-12 envelope schema, never an execution authorization."""
    schema = ActivityEnvelope.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$comment"] = (
        "Structural activity envelope only. Use SDK validators for lifetime and complete byte/"
        "depth/item bounds; verify detached signatures against independent trust. Current "
        "runtime bindings, time, deadlines and authority remain unchecked."
    )
    schema["allOf"] = [{"oneOf": [
        {"properties": {
            "kind": {"const": "connector_read"},
            "dependency": {"properties": {"kind": {"const": "connector"}}},
            "operation": {"type": "string"},
        }},
        {"properties": {
            "kind": {"const": "model"},
            "dependency": {"properties": {"kind": {"const": "model"}}},
            "operation": {"type": "null"},
        }},
    ]}]
    return schema
