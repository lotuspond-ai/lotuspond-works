"""Bounded agent activities: model rounds and declared read tools as child attempts.

The agent's own (root) attempt calls no provider. Every model round and every tool
call is admitted by the control plane as a child attempt under the root budget,
delivered as its own signed delivery and claimed separately; the parent's lease is
never permission for another call. Each child's signed delivery must name exactly
the kind, dependency, operation and stored arguments the agent asked for, and a
tool read runs through the same connection, secret and bound checks as a root read.

A model reply is data: a proposed tool must be a declared one and its arguments
must match that tool's pinned schemas, or nothing is invoked. Resolved context,
instructions and the transcript stay in worker memory and reach only the model
child: a model round stores the digest of its request and of its reply, never
their content. Output is checked against the pinned output schema before it is
reported.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import rfc8785
from lotuspond_sdk.executable.package import ArtifactRef

from .activity_invocation import require
from .activity_protocol import ActivityResult, ChildActivityRequest
from .context import ContextReader, ContextRef
from .errors import FleetError
from .providers import (
    ActivityFailure,
    ModelReply,
    ModelRequest,
    ProviderResult,
    ToolProposal,
    ToolSpec,
)

MAX_STEPS = 64
MAX_TOOLS = 64
MAX_CONTEXT = 64
MAX_PROPOSALS = 16
_LIMIT = 2**53 - 1


class ProposalRefused(FleetError):
    """A model proposal naming no declared tool, or carrying any other arguments."""

    def __init__(self):
        super().__init__("forbidden")


def _limit(value):
    return type(value) is int and 0 <= value <= _LIMIT


@dataclass(frozen=True)
class AgentRecords:
    """Pinned agent declarations for one root attempt, from the control plane."""

    model: ArtifactRef
    model_name: str
    instructions: str
    tools: tuple[ToolSpec, ...]
    context: tuple[ContextRef, ...]
    max_steps: int
    max_input_tokens: int
    max_output_tokens: int
    max_cost_microusd: int

    def __post_init__(self):
        names = [tool.name for tool in self.tools] if type(self.tools) is tuple else None
        if (
            type(self.model) is not ArtifactRef
            or self.model.kind != "model"
            or type(self.model_name) is not str
            or type(self.instructions) is not str
            or names is None
            or len(names) > MAX_TOOLS
            or len(set(names)) != len(names)
            or any(type(tool) is not ToolSpec for tool in self.tools)
            or type(self.context) is not tuple
            or len(self.context) > MAX_CONTEXT
            or any(type(ref) is not ContextRef for ref in self.context)
            or type(self.max_steps) is not int
            or not 1 <= self.max_steps <= MAX_STEPS
            or not all(
                _limit(value)
                for value in (self.max_input_tokens, self.max_output_tokens, self.max_cost_microusd)
            )
        ):
            raise ValueError("Invalid agent declarations")


@dataclass(frozen=True)
class AgentRequest:
    attempt_id: str
    deadline_ms: int
    input: object
    output_schema: object
    records: AgentRecords

    @classmethod
    def from_records(cls, deps, envelope, records, value):
        agent = deps.records.agent(envelope)
        if type(agent) is not AgentRecords or agent.model != envelope.dependency:
            raise FleetError("command_rejected")
        return cls(envelope.attempt_id, records.deadline_ms, value, records.output_schema, agent)


def _tool(spec: ToolSpec, proposal: ToolProposal):
    """A declared tool's actual arguments, exactly typed; anything else is refused."""
    from .activity_worker import _typed

    arguments = proposal.arguments
    if type(arguments) is not dict or set(arguments) != {"target", "input"}:
        raise ProposalRefused
    try:
        definitions = spec.input_schema.get("definitions", [])
        for key in ("target", "input"):
            _typed(arguments[key], {"schema": spec.input_schema[key], "definitions": definitions})
    except Exception:
        raise ProposalRefused from None
    return arguments


def _reply(raw, usage) -> ModelReply:
    try:
        proposed = raw.get("tool_calls", [])
        if type(proposed) is not list or len(proposed) > MAX_PROPOSALS:
            raise ValueError
        calls = tuple(ToolProposal(item["name"], item["arguments"]) for item in proposed)
        return ModelReply(raw.get("output"), calls, usage)
    except Exception:
        raise ActivityFailure("invalid_output") from None


def _digest(value) -> str:
    return hashlib.sha256(rfc8785.dumps(value)).hexdigest()


class _Limits:
    """What the agent may still spend across its model rounds."""

    def __init__(self, records: AgentRecords):
        self.input = records.max_input_tokens
        self.output = records.max_output_tokens
        self.cost = records.max_cost_microusd

    def spend(self, usage):
        spent = (usage.input_tokens, usage.output_tokens, usage.cost_microusd)
        if any(value is None for value in spent):
            # Unknown spending cannot bound another round.
            raise ActivityFailure("provider_failed")
        self.input -= usage.input_tokens
        self.output -= usage.output_tokens
        self.cost -= usage.cost_microusd
        if min(self.input, self.output, self.cost) < 0:
            raise ActivityFailure("provider_failed")  # the model ignored its limits

    def spend_cost(self, usage):
        """A tool read costs money but no model tokens; unknown cost ends the agent."""
        if usage.cost_microusd is None:
            raise ActivityFailure("provider_failed")
        self.cost -= usage.cost_microusd
        if self.cost < 0:
            raise ActivityFailure("provider_failed")

    def exhausted(self):
        return min(self.input, self.output, self.cost) <= 0


class _Children:
    """Admission, delivery, verification, claim, supervised call and report of a child."""

    def __init__(self, request: AgentRequest, deps, keeper):
        self.request, self.deps, self.keeper = request, deps, keeper
        self.count = 0
        self.started = False  # whether any child may have reached a provider

    def _receive(self, kind, name, stored, dependency, operation):
        """Admit a child and take its delivery, bound to exactly what was asked."""
        from .activity_worker import _canonical, declinable, decline, retry, verify_delivery

        deps, root, keeper = self.deps, self.request.attempt_id, self.keeper
        content = _canonical(stored)
        handle = retry(lambda: deps.client.store(root, content), deps, keeper)
        self.count += 1
        request = ChildActivityRequest(
            request_id=f"{kind}-{self.count}",
            parent_attempt_id=root,
            kind=kind,
            declaration_name=name,
            arguments=handle,
        )
        try:
            child_id = retry(lambda: deps.client.request_child(request), deps, keeper)
            # Only this parent's children: an unrelated delivery is never handed over here.
            delivery = retry(lambda: deps.client.poll(parent_attempt_id=root), deps, keeper)
        except FleetError:
            # Refused (shared budget spent, authority gone): nothing was invoked.
            raise ActivityFailure("provider_failed", reached=False) from None
        if delivery is None:
            # Not dispatched (too close to the deadline, for example): the root's
            # outcome ends the child; nothing was invoked.
            raise ActivityFailure("unavailable", reached=False)
        try:
            envelope, records = verify_delivery(delivery, deps)
            if (
                envelope.attempt_id != child_id
                or envelope.kind != kind
                or envelope.dependency != dependency
                or envelope.operation != operation
                or envelope.input != handle
            ):
                raise FleetError("command_rejected")
        except FleetError as error:
            # Not the child this agent asked for: never claimed, never invoked; a bad
            # delivery is also declined so it heads no later poll.
            if declinable(error):
                decline(deps, delivery)
            raise ActivityFailure("provider_failed", reached=False) from None
        return delivery, envelope, records

    def call(self, kind, name, stored, dependency, operation, invoke, finish):
        """One child to its reported outcome.

        `invoke(envelope, records, keeper, deadline_ms)` runs the provider;
        `finish(reply, records)` turns its reply into `(value, stored_result)`.
        """
        from .activity_worker import (
            TRANSIENT,
            LeaseKeeper,
            LeaseLost,
            _canonical,
            checked_lease,
            delivery_digest,
            failure_code,
            observed_usage,
            retry,
            zero,
        )

        deps = self.deps
        # Stamped before anything starts: a clock failure later never turns into zero.
        nothing = zero(deps)
        delivery, envelope, records = self._receive(kind, name, stored, dependency, operation)
        child_id = envelope.attempt_id
        deps.journal.advance(
            child_id,
            "claimed",
            delivery_sha256=delivery_digest(delivery),
            generation=envelope.generation,
        )
        try:
            granted = retry(lambda: deps.client.claim(delivery), deps, self.keeper)
        except FleetError as error:
            if error.code in TRANSIENT:
                # The claim may have committed, and a claimed child keeps the root
                # from reporting: both are left to the lapse and reconciliation.
                deps.journal.advance(child_id, "abandoned")
                raise LeaseLost from None
            raise ActivityFailure("provider_failed", reached=False) from None
        try:
            lease = checked_lease(granted, envelope, deps)
        except FleetError as error:
            # Granted out of bounds: nothing runs, and the child is reported like a
            # root in the same case, with zero usage, so the root can report too.
            self._report(
                ActivityResult(
                    attempt_id=child_id,
                    lease_revision=granted.lease_revision,
                    result=None,
                    code=failure_code(error),
                    usage=nothing,
                )
            )
            raise ActivityFailure(failure_code(error), reached=False) from None
        deps.journal.advance(child_id, "started")
        self.started = True
        child_keeper = LeaseKeeper(deps, lease, parent=self.keeper)
        usage, value, handle, code = nothing, None, None, "succeeded"
        try:
            deadline = min(records.deadline_ms, lease.deadline_ms)
            outcome = invoke(envelope, records, child_keeper, deadline)
            usage = observed_usage(outcome, deps)
            if child_keeper.lost:
                raise LeaseLost
            value, result = finish(require(outcome), records)
            content = _canonical(result)
            handle = retry(lambda: deps.client.store(child_id, content), deps, child_keeper)
        except ActivityFailure as failure:
            code = failure.code
        except FleetError as error:
            if error.code == "unsafe_storage":
                raise
            # After the provider ran (its value could not be stored): final, never a
            # retryable code that would run the round or read again.
            code = "provider_failed"
        except LeaseLost:
            deps.journal.advance(child_id, "abandoned")
            raise
        if child_keeper.lost:
            deps.journal.advance(child_id, "abandoned")
            raise LeaseLost
        self._report(
            ActivityResult(
                attempt_id=child_id,
                lease_revision=child_keeper.lease.lease_revision,
                result=handle if code == "succeeded" else None,
                code=code,
                usage=usage,
            )
        )
        if code != "succeeded":
            raise ActivityFailure(code)  # the agent ends with its child's code
        return value

    def _report(self, result: ActivityResult):
        """Journal a child's outcome, then report it while the parent's lease holds."""
        from .activity_worker import LeaseLost, retry

        deps = self.deps
        outcome = result.model_dump(mode="json")
        deps.journal.advance(result.attempt_id, "finished", outcome=outcome)
        try:
            retry(lambda: deps.client.result(result), deps, self.keeper)
        except FleetError:
            # An unreported child keeps the root from reporting too: both lapse to
            # reconciliation, with this outcome kept in the journal as evidence.
            raise LeaseLost from None
        deps.journal.advance(result.attempt_id, "recorded", result_sha256=_digest(outcome))


def run_agent(request: AgentRequest, deps, keeper) -> ProviderResult:
    """Model rounds and declared tools until typed output, a limit or a refusal.

    Once any child may have reached a provider, a control-plane error ends the
    agent with `provider_failed`, never a retryable code that would rerun it.
    """
    children = _Children(request, deps, keeper)
    try:
        return _run(request, deps, keeper, children)
    except FleetError as error:
        if type(error) is ProposalRefused or error.code == "unsafe_storage":
            raise
        if not children.started:
            raise
        raise ActivityFailure("provider_failed") from None


def _run(request: AgentRequest, deps, keeper, children) -> ProviderResult:
    from .activity_worker import (
        LeaseLost,
        _typed,
        bounded_response,
        invoke_model,
        invoke_read,
        observed_usage,
        read_arguments,
        zero,
    )

    records = request.records
    model = deps.providers.select(records.model, "model")
    tools = {spec.name: spec for spec in records.tools}
    # Reread under current access for this activity; kept only in worker memory.
    # The lease is kept live between reads.
    reader, context = ContextReader(deps.context, request.attempt_id), []
    for ref in records.context:
        if not keeper.tick():
            raise LeaseLost
        context.extend(reader.read_all((ref,)))
    context = tuple(context)
    limits = _Limits(records)
    transcript = []

    def model_finish(raw, child_records):
        reply = _reply(raw, None)
        # The round's stored result is the reply's digest; its content stays here.
        stored = {"reply_sha256": _digest(raw)}
        return (reply, raw), _typed(stored, child_records.output_schema)

    for step in range(records.max_steps):
        if limits.exhausted():
            raise ActivityFailure("provider_failed")  # nothing left to give a model round
        model_request = ModelRequest(
            model=records.model,
            instructions=records.instructions,
            input=request.input,
            context=context,
            output_schema=request.output_schema,
            tools=records.tools,
            transcript=tuple(transcript),
            max_input_tokens=limits.input,
            max_output_tokens=limits.output,
            max_cost_microusd=limits.cost,
        )
        stored = {"round": step + 1, "request_sha256": _digest(model_request.to_json())}
        usage_seen = []

        def invoke(
            envelope,
            child_records,
            cancel,
            deadline_ms,
            model_request=model_request,
            seen=usage_seen,
        ):
            outcome = invoke_model(
                deps, model, model_request, cancel, deadline_ms, attempt_id=envelope.attempt_id
            )
            seen.append(observed_usage(outcome, deps))
            return outcome

        reply, _ = children.call(
            "model", records.model_name, stored, records.model, None, invoke, model_finish
        )
        limits.spend(usage_seen[-1])
        if not reply.tool_calls:
            # A reply without tool proposals is final; its output (possibly null) is
            # checked by the caller. The root attempt itself called no provider: its
            # own usage is zero, each round and tool reported usage on its own child.
            return ProviderResult(reply.output, zero(deps))
        # Every proposal is checked, and its provider selected, before any of them runs.
        checked = []
        for proposal in reply.tool_calls:
            spec = tools.get(proposal.name) if type(proposal.name) is str else None
            if spec is None:
                raise ProposalRefused
            arguments = _tool(spec, proposal)
            deps.providers.select(spec.dependency, "read", spec.operation)
            checked.append((spec, arguments))
        for spec, arguments in checked:
            read_seen = []

            def read(
                envelope, child_records, cancel, deadline_ms, arguments=arguments, seen=read_seen
            ):
                typed = read_arguments(arguments, child_records)
                outcome = invoke_read(deps, envelope, child_records, typed, cancel, deadline_ms)
                seen.append(observed_usage(outcome, deps))
                return outcome

            def read_finish(raw, child_records):
                value = bounded_response(raw.get("value"), child_records)
                value = _typed(value, child_records.output_schema)
                return value, value

            value = children.call(
                "connector_read",
                spec.name,
                arguments,
                spec.dependency,
                spec.operation,
                read,
                read_finish,
            )
            limits.spend_cost(read_seen[-1])  # tools draw on the same cost limit
            transcript.append({"tool": spec.name, "arguments": arguments, "result": value})
    raise ActivityFailure("invalid_output")  # the step limit ended the agent
