"""Read and model provider contracts for activities (E04).

Providers are constructor-selected through fixed descriptors; nothing is discovered,
loaded by path or chosen by a model, signal or envelope. A descriptor pins the exact
catalog document, its effect class, the installed connector artifact and the
operations it may perform; a missing or changed pin denies. With no configured real
provider an activity reports `unavailable`, never success. Synthetic providers are
test-only.

A provider runs inside the fixed provider child (`activity_child`), which builds
the frozen request types below and calls `ReadProvider.invoke` or
`ModelProvider.generate` on the implementation registered under the descriptor's
fixed identifier.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Literal, Protocol

import rfc8785
from lotuspond_sdk.activity import UsageObservation
from lotuspond_sdk.executable.package import ArtifactRef

from .activity_protocol import ResultCode, tool_name
from .protocol import ConnectorPin, SecretPin

FAILURES = frozenset(code for code in ResultCode.__args__ if code != "succeeded")


class ActivityFailure(Exception):
    """A fixed outcome code; provider, prompt and customer text never travel with it.

    `usage` is what the provider reported before failing, if anything; `reached` is
    False only when the provider positively sent nothing (zero usage is then known),
    and None when that is not known.
    """

    def __init__(self, code: str, *, usage: UsageObservation | None = None, reached=None):
        self.code = code if type(code) is str and code in FAILURES else "provider_failed"
        self.usage = usage if type(usage) is UsageObservation else None
        self.reached = reached if reached is False else None
        super().__init__(self.code)


class Fenced(ActivityFailure):
    """Earlier provider cleanup was not confirmed: no further provider work."""

    def __init__(self):
        super().__init__("unavailable", reached=False)


def _artifact(value) -> ArtifactRef:
    # A private copy: the caller's object cannot change the descriptor's pin.
    return ArtifactRef.model_validate(ArtifactRef.model_validate(value).model_dump())


@dataclass(frozen=True)
class ProviderDescriptor:
    """Immutable selection record for one provider.

    `connector` is the installed connector artifact a read provider serves; a
    connection is used only when its pin equals it. `credentials` are worker-local
    secret pins a provider needs (a model's API key, for example), resolved by the
    worker just before each call. `script` is test-only behaviour data for a
    synthetic provider, kept as canonical JSON text; never code.
    """

    provider_id: str
    dependency: ArtifactRef
    effect: Literal["read", "model"]
    operations: frozenset = field(default_factory=frozenset)
    test_only: bool = False
    script: object = field(default=None, compare=False, hash=False)
    connector: ConnectorPin | None = None
    credentials: tuple = ()

    def __post_init__(self):
        object.__setattr__(self, "dependency", _artifact(self.dependency))
        if self.effect not in ("read", "model") or type(self.operations) is not frozenset:
            raise ValueError("Invalid provider descriptor")
        if (self.effect == "read") != (self.dependency.kind == "connector"):
            raise ValueError("Effect and dependency kind disagree")
        if self.effect == "model" and (self.operations or self.connector is not None):
            raise ValueError("A model provider has no operations or connector")
        if self.effect == "read":
            connector = ConnectorPin.model_validate(self.connector)
            if (connector.name, connector.version) != (
                self.dependency.name,
                self.dependency.version,
            ):
                raise ValueError("Connector pin does not match the catalog dependency")
            object.__setattr__(self, "connector", connector)
        if type(self.credentials) is not tuple or len(self.credentials) > 16:
            raise ValueError("Invalid credentials")
        object.__setattr__(
            self, "credentials", tuple(SecretPin.model_validate(pin) for pin in self.credentials)
        )
        if self.script is not None:
            if not self.test_only:
                raise ValueError("Only a synthetic provider carries a script")
            object.__setattr__(self, "script", rfc8785.dumps(self.script).decode("utf-8"))

    def script_data(self):
        return None if self.script is None else json.loads(self.script)


@dataclass(frozen=True)
class ToolSpec:
    """One declared read tool, as presented to a model and checked on its proposals."""

    name: str
    dependency: ArtifactRef
    operation: str
    input_schema: object
    output_schema: object

    def __post_init__(self):
        if tool_name(self.dependency.name, self.operation) != self.name:
            raise ValueError("Tool name does not match its pinned declaration")


@dataclass(frozen=True)
class ModelRequest:
    """One model round. Built in worker memory and sent only to the provider child:
    resolved context and the transcript are never stored as execution values."""

    model: ArtifactRef
    instructions: str
    input: object
    context: tuple
    output_schema: object
    tools: tuple[ToolSpec, ...]
    transcript: tuple
    max_input_tokens: int
    max_output_tokens: int
    max_cost_microusd: int

    def to_json(self) -> dict:
        return {
            "model": self.model.model_dump(mode="json"),
            "instructions": self.instructions,
            "input": self.input,
            "context": list(self.context),
            "output_schema": self.output_schema,
            "tools": [
                {
                    "name": tool.name,
                    "dependency": tool.dependency.model_dump(mode="json"),
                    "operation": tool.operation,
                    "input_schema": tool.input_schema,
                    "output_schema": tool.output_schema,
                }
                for tool in self.tools
            ],
            "transcript": list(self.transcript),
            "max_input_tokens": self.max_input_tokens,
            "max_output_tokens": self.max_output_tokens,
            "max_cost_microusd": self.max_cost_microusd,
        }

    @classmethod
    def from_json(cls, raw: dict) -> ModelRequest:
        return cls(
            model=ArtifactRef.model_validate(raw["model"]),
            instructions=raw["instructions"],
            input=raw["input"],
            context=tuple(raw["context"]),
            output_schema=raw["output_schema"],
            tools=tuple(
                ToolSpec(
                    name=tool["name"],
                    dependency=ArtifactRef.model_validate(tool["dependency"]),
                    operation=tool["operation"],
                    input_schema=tool["input_schema"],
                    output_schema=tool["output_schema"],
                )
                for tool in raw["tools"]
            ),
            transcript=tuple(raw["transcript"]),
            max_input_tokens=raw["max_input_tokens"],
            max_output_tokens=raw["max_output_tokens"],
            max_cost_microusd=raw["max_cost_microusd"],
        )


@dataclass(frozen=True)
class ToolProposal:
    """Model output: data naming a declared tool, never permission to call it."""

    name: object
    arguments: object


@dataclass(frozen=True)
class ModelReply:
    output: object | None
    tool_calls: tuple[ToolProposal, ...]
    usage: UsageObservation


@dataclass(frozen=True)
class ProviderResult:
    value: object
    usage: UsageObservation


class ReadProvider(Protocol):
    descriptor: ProviderDescriptor

    def invoke(self, records, arguments: object, deadline_ms: int) -> ProviderResult: ...


class ModelProvider(Protocol):
    descriptor: ProviderDescriptor

    def generate(self, request: ModelRequest, deadline_ms: int) -> ModelReply: ...


class ProviderSet:
    """The constructor-selected providers of one worker, keyed by exact pins."""

    def __init__(self, descriptors=(), *, test_mode=False):
        found = {}
        for descriptor in descriptors:
            if type(descriptor) is not ProviderDescriptor:
                raise ValueError("Providers are selected by descriptor")
            if descriptor.test_only and test_mode is not True:
                raise ValueError("Synthetic providers require test mode")
            key = (descriptor.dependency.sha256, descriptor.effect)
            if key in found:
                raise ValueError("One provider per pinned dependency")
            found[key] = descriptor
        self._found, self._test_mode = found, test_mode is True

    @property
    def test_mode(self) -> bool:
        return self._test_mode

    def select(self, dependency, effect, operation=None) -> ProviderDescriptor:
        """The descriptor for exactly this pin and effect; anything else is unavailable.

        `unavailable` means this runtime cannot serve the pin; another runtime with
        the right provider installed may, within the attempt's retry bounds.
        """
        dependency = ArtifactRef.model_validate(dependency)
        descriptor = self._found.get((dependency.sha256, effect))
        if descriptor is None:
            raise ActivityFailure("unavailable", reached=False)
        if descriptor.dependency != dependency:
            raise ActivityFailure("unavailable", reached=False)  # a changed pin never matches
        if effect == "read" and operation not in descriptor.operations:
            raise ActivityFailure("unavailable", reached=False)  # a read provider never writes
        return descriptor


def unknown_usage(completed_at_ms: int) -> UsageObservation:
    """Usage that was not observed stays unknown, never zero."""
    return UsageObservation(
        input_tokens=None, output_tokens=None, cost_microusd=None, completed_at_ms=completed_at_ms
    )


def zero_usage(completed_at_ms: int) -> UsageObservation:
    """Nothing was sent: zero is known."""
    return UsageObservation(
        input_tokens=0, output_tokens=0, cost_microusd=0, completed_at_ms=completed_at_ms
    )


__all__ = [
    "ActivityFailure",
    "Fenced",
    "ModelProvider",
    "ModelReply",
    "ModelRequest",
    "ProviderDescriptor",
    "ProviderResult",
    "ProviderSet",
    "ReadProvider",
    "ToolProposal",
    "ToolSpec",
    "unknown_usage",
    "zero_usage",
]
