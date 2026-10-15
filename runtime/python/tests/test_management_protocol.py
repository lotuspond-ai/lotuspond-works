"""Management DTOs retain strict bounded Python/JSON contracts without API imports."""

import pytest
from pydantic import TypeAdapter

from lotuspond_runtime.errors import FleetError
from lotuspond_runtime.protocol import (
    ConnectionList,
    ConnectionVersionDetail,
    ConnectionView,
    EmptyManagementRequest,
    ManagementNameRequest,
    ManagementPageRequest,
    ManagementStatusRequest,
    PoolPolicyDetail,
    PoolView,
    SecretRegistrationRequest,
)

SCOPE = {
    "workspace_id": "00000000-0000-0000-0000-000000000001",
    "project_id": "00000000-0000-0000-0000-000000000002",
    "environment_id": "00000000-0000-0000-0000-000000000003",
}


@pytest.mark.parametrize(
    "model,raw",
    [
        (ManagementNameRequest, {"name": 5}),
        (ManagementStatusRequest, {"status": "deleted"}),
        (SecretRegistrationRequest, {"binding_id": "/private/sentinel", "version": 1}),
        (SecretRegistrationRequest, {"binding_id": "binding", "version": True}),
        (SecretRegistrationRequest, {"binding_id": "binding", "version": 1.0}),
        (ManagementPageRequest, {"limit": True}),
        (ManagementPageRequest, {"limit": 101}),
        (ManagementPageRequest, {"after_id": "unknown"}),
        (EmptyManagementRequest, {"token": "synthetic"}),
        (ConnectionList, {"items": [], "next_after_id": None, "count": 0}),
    ],
)
def test_management_dtos_refuse_coercion_unknowns_and_unsafe_metadata(model, raw):
    with pytest.raises(FleetError, match="^invalid_request$"):
        model.model_validate(raw)


@pytest.mark.parametrize(
    "model",
    [
        ManagementNameRequest,
        ManagementStatusRequest,
        ManagementPageRequest,
        SecretRegistrationRequest,
        ConnectionView,
        PoolView,
        ConnectionVersionDetail,
        PoolPolicyDetail,
    ],
)
def test_management_native_json_adapter_stays_denied(model):
    with pytest.raises(FleetError, match="^invalid_request$"):
        TypeAdapter(model).validate_json(b"{}")


def test_safe_inventory_view_exact_fields_and_model_instance_revalidation():
    raw = {
        "id": "00000000-0000-0000-0000-000000000004",
        "scope": SCOPE,
        "name": "Connection",
        "status": "active",
        "current_version": 1,
        "created_at_ms": 0,
    }
    item = ConnectionView.model_validate(raw)
    assert item.model_dump(mode="json") == raw
    object.__setattr__(item, "created_at_ms", True)
    with pytest.raises(FleetError):
        ConnectionView.model_validate(item)
