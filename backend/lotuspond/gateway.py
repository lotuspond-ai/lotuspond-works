from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal
from time import monotonic
from typing import Annotated, Any, Literal

import httpx
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError, field_validator
from sqlalchemy import func, select

from .config import ProviderModel, Settings
from .deps import require
from .models import GatewayUsage, Workspace, new_id, now
from .security import Principal, fail

router = APIRouter(prefix="/api/v1/gateway")
MONEY_QUANTUM = Decimal("0.000001")


class GatewayInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Message(GatewayInput):
    role: Literal["system", "user", "assistant"]
    content: str = Field(max_length=64_000)


class CompletionInput(GatewayInput):
    model: str = Field(min_length=1, max_length=160)
    messages: list[Message] = Field(min_length=1, max_length=100)
    max_tokens: StrictInt = Field(default=1024, ge=1, le=8192)
    temperature: float = Field(default=0, strict=True, ge=0, le=2)
    stream: Literal[False] = False

    @field_validator("temperature", mode="before")
    @classmethod
    def temperature_is_number(cls, value):
        if isinstance(value, bool):
            raise ValueError("Temperature must be a finite number")
        return value

    @field_validator("stream", mode="before")
    @classmethod
    def streaming_is_disabled(cls, value):
        if value is not False:
            raise ValueError("Streaming is not supported by this endpoint")
        return value


class ProviderMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    role: str
    content: str | None


class ProviderChoice(BaseModel):
    model_config = ConfigDict(extra="allow")
    message: ProviderMessage


class ProviderUsage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    prompt_tokens: StrictInt = Field(ge=0, le=1_000_000_000_000_000)
    completion_tokens: StrictInt = Field(ge=0, le=1_000_000_000_000_000)


class ProviderCompletion(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    choices: list[ProviderChoice]
    usage: ProviderUsage


class ProviderFailure(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def provider_client(settings: Settings) -> httpx.AsyncClient:
    """A provider client that ignores ambient proxy configuration and never follows redirects."""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(settings.provider_timeout_seconds),
        follow_redirects=False,
        trust_env=False,
    )


def _configured(settings: Settings) -> bool:
    return bool(
        settings.network_mode == "connected"
        and settings.provider_url
        and settings.provider_key
        and settings.provider_key.get_secret_value()
        and settings.provider_models
    )


def _model(settings: Settings, identifier: str) -> ProviderModel | None:
    return next((model for model in settings.provider_models if model.id == identifier), None)


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_CEILING)


def _maximum_cost(body: CompletionInput, model: ProviderModel) -> Decimal:
    # This conservative estimate includes every request byte and the requested output allowance.
    # Provider-side framing can still exceed it; settlement charges that known overrun in full.
    payload = body.model_dump(mode="json")
    input_token_bound = len(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode(
            "utf-8"
        )
    )
    raw = (
        Decimal(input_token_bound) * model.input_usd_per_million
        + Decimal(body.max_tokens) * model.output_usd_per_million
    ) / Decimal(1_000_000)
    return _money(raw)


def _month_start(current: datetime | None = None) -> datetime:
    current = current or now()
    current = current.replace(tzinfo=current.tzinfo or UTC)
    return datetime(current.year, current.month, 1, tzinfo=UTC)


def _workspace_lock(workspace_id: str):
    return select(Workspace).where(Workspace.id == workspace_id).with_for_update()


def reserve_usage(sessions, workspace_id: str, model: str, maximum_cost: Decimal) -> str:
    """Serialize budget decisions on the workspace row and commit before provider traffic."""
    with sessions() as db, db.begin():
        workspace = db.scalar(_workspace_lock(workspace_id))
        if workspace is None or workspace.status != "active":
            fail(403, "workspace_suspended", "Workspace access is suspended.")
        charged = db.scalar(
            select(func.coalesce(func.sum(GatewayUsage.charged_usd), 0)).where(
                GatewayUsage.workspace_id == workspace_id,
                GatewayUsage.created_at >= _month_start(),
            )
        )
        spent = Decimal(charged or 0)
        if workspace.monthly_budget_usd is not None and spent + maximum_cost > Decimal(
            workspace.monthly_budget_usd
        ):
            fail(
                402,
                "budget_exceeded",
                "The request would exceed the workspace monthly budget.",
            )
        identifier = new_id()
        db.add(
            GatewayUsage(
                id=identifier,
                workspace_id=workspace_id,
                model=model,
                status="reserved",
                reserved_usd=maximum_cost,
                charged_usd=maximum_cost,
            )
        )
    return identifier


def _settle_uncertain(sessions, identifier: str, latency_ms: int, error_code: str) -> None:
    with sessions() as db, db.begin():
        usage = db.scalar(
            select(GatewayUsage).where(GatewayUsage.id == identifier).with_for_update()
        )
        if usage is None or usage.status != "reserved":
            raise RuntimeError("Gateway usage reservation is unavailable")
        usage.status = "uncertain"
        usage.latency_ms = latency_ms
        usage.error_code = error_code
        usage.completed_at = now()


def _settle_success(
    sessions,
    identifier: str,
    response: ProviderCompletion,
    model: ProviderModel,
    latency_ms: int,
) -> None:
    actual = _money(
        (
            Decimal(response.usage.prompt_tokens) * model.input_usd_per_million
            + Decimal(response.usage.completion_tokens) * model.output_usd_per_million
        )
        / Decimal(1_000_000)
    )
    with sessions() as db, db.begin():
        usage = db.scalar(
            select(GatewayUsage).where(GatewayUsage.id == identifier).with_for_update()
        )
        if usage is None or usage.status != "reserved":
            raise RuntimeError("Gateway usage reservation is unavailable")
        overrun = actual > usage.reserved_usd
        usage.status = "settled"
        usage.charged_usd = actual
        usage.actual_usd = actual
        usage.prompt_tokens = response.usage.prompt_tokens
        usage.completion_tokens = response.usage.completion_tokens
        usage.provider_request_id = response.id[:160]
        usage.latency_ms = latency_ms
        usage.error_code = "budget_overrun" if overrun else None
        usage.completed_at = now()
    if overrun:
        raise ProviderFailure(
            "provider_usage_exceeded_reservation",
            "The provider returned usage outside the reserved request estimate.",
        )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object member")
        result[key] = value
    return result


def _provider_json(raw: bytes) -> ProviderCompletion:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Non-finite number")),
            object_pairs_hook=_unique_object,
        )
        return ProviderCompletion.model_validate(value)
    except (UnicodeDecodeError, ValueError, ValidationError) as exc:
        raise ProviderFailure(
            "provider_response_invalid",
            "The provider returned an unsupported completion response.",
        ) from exc


def _read_provider(settings: Settings, body: CompletionInput) -> ProviderCompletion:
    # The FastAPI endpoint is synchronous and runs in its worker thread; keep database
    # accounting there while an async transport makes the total network deadline cancellable.
    return asyncio.run(_read_provider_async(settings, body))


async def _read_provider_async(settings: Settings, body: CompletionInput) -> ProviderCompletion:
    url = f"{settings.provider_url}/chat/completions"
    key = settings.provider_key.get_secret_value() if settings.provider_key else ""
    try:
        async with (
            asyncio.timeout(settings.provider_timeout_seconds),
            provider_client(settings) as client,
        ):
            async with client.stream(
                "POST",
                url,
                headers={"authorization": f"Bearer {key}", "content-type": "application/json"},
                json=body.model_dump(mode="json"),
            ) as response:
                if response.is_redirect:
                    raise ProviderFailure(
                        "provider_redirect_rejected", "The model provider returned a redirect."
                    )
                if not response.is_success:
                    raise ProviderFailure(
                        "provider_rejected", "The model provider rejected the request."
                    )
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(chunk) > settings.provider_response_bytes - len(raw):
                        raise ProviderFailure(
                            "provider_response_too_large",
                            "The provider response exceeded the size limit.",
                        )
                    raw.extend(chunk)
    except ProviderFailure:
        raise
    except (TimeoutError, httpx.TimeoutException, httpx.RequestError) as exc:
        raise ProviderFailure(
            "provider_unavailable", "The model provider could not be reached."
        ) from exc
    return _provider_json(bytes(raw))


@router.get("/models")
def models(request: Request, _: Annotated[Principal, Depends(require("gateway"))]):
    settings: Settings = request.app.state.settings
    return {
        "object": "list",
        "data": [{"id": model.id, "object": "model"} for model in settings.provider_models],
        "configured": _configured(settings),
    }


@router.post("/chat/completions")
def completion(
    body: CompletionInput,
    request: Request,
    principal: Annotated[Principal, Depends(require("gateway"))],
):
    settings: Settings = request.app.state.settings
    if settings.network_mode == "airgap":
        fail(503, "network_disabled", "Outbound model execution is disabled in airgap mode.")
    model = _model(settings, body.model)
    if not model or not _configured(settings):
        fail(
            503,
            "provider_not_configured",
            "An administrator must configure this model and its provider before execution.",
        )
    request.app.state.limiter.check(f"gateway:{principal.workspace_id}", 60)
    maximum_cost = _maximum_cost(body, model)
    identifier = reserve_usage(
        request.app.state.sessions, principal.workspace_id, model.id, maximum_cost
    )
    started = monotonic()
    try:
        output = _read_provider(settings, body)
        latency_ms = max(0, round((monotonic() - started) * 1000))
        _settle_success(request.app.state.sessions, identifier, output, model, latency_ms)
    except ProviderFailure as exc:
        latency_ms = max(0, round((monotonic() - started) * 1000))
        # A known usage overrun has already been settled at its full actual amount.
        with request.app.state.sessions() as db:
            current = db.get(GatewayUsage, identifier)
            is_reserved = current is not None and current.status == "reserved"
        if is_reserved:
            _settle_uncertain(request.app.state.sessions, identifier, latency_ms, exc.code)
        fail(502, exc.code, exc.message)
    return output.model_dump(mode="json")
