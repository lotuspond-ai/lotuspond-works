// Helpers for the Operate pages (Human tasks and Control Room). Execution routes live under one
// project and environment, report times as epoch milliseconds, and answer errors with closed
// codes (backend/lotuspond/execution/errors.py) that never carry a submitted value.

export type Scope = { projectId: string; environmentId: string };

export function scopedPath(scope: Scope, path: string) {
  return `/projects/${encodeURIComponent(scope.projectId)}/environments/${encodeURIComponent(scope.environmentId)}/${path}`;
}

// Epoch milliseconds as the ISO text the console's time helpers take.
export function fromMs(value: unknown): string | undefined {
  return typeof value === 'number' && Number.isFinite(value)
    ? new Date(value).toISOString()
    : undefined;
}

// A fresh request identity: an OpaqueId (`^[A-Za-z0-9][A-Za-z0-9._:-]*$`, at most 128).
export function opaqueId(prefix: string, random: () => string = () => crypto.randomUUID()) {
  return `${prefix}-${random()}`;
}

const messages: Record<string, string> = {
  authority_unavailable:
    'This server has not switched on production execution for this environment yet, so there is nothing here to act on.',
  forbidden:
    'Your role or session cannot do this here. Reviews need a person signed in, not a key.',
  not_found: 'It is no longer here. It may have finished, expired or been removed.',
  conflict: 'Someone or something changed it first. The latest state has been loaded.',
  invalid_request: 'The request was not accepted as sent.',
  dependency_unavailable: 'A service it depends on is unavailable. Try again shortly.',
  capacity_unavailable: 'The engine is at capacity. Try again shortly.',
};

export function operateMessage(code: string | undefined, fallback: string) {
  return (code && messages[code]) || fallback;
}

// A refused Allow on a review that needs independent approval: the person who requested the
// action is signed in but may not allow it, so say that rather than blame their session.
export function decisionMessage(
  code: string | undefined,
  decision: 'allow' | 'deny',
  independent: boolean,
  fallback: string,
) {
  if (code === 'forbidden' && decision === 'allow' && independent)
    return 'This action needs independent approval, and you requested it. Release the task so another person can allow it.';
  return operateMessage(code, fallback);
}

export type ExecutionState =
  'pending' | 'running' | 'waiting' | 'paused' | 'cancelled' | 'succeeded' | 'failed' | string;

export const FINISHED = new Set(['cancelled', 'succeeded', 'failed']);

// The controls an execution offers, from its observed and requested state.
export type Control = 'pause' | 'resume' | 'cancel';

export function allowedControls(state: ExecutionState, requested: string | undefined): Control[] {
  const controls: Control[] = [];
  if (FINISHED.has(state) || requested === 'cancelled') return controls;
  if (state === 'paused' || requested === 'paused') controls.push('resume');
  else controls.push('pause');
  controls.push('cancel');
  return controls;
}

export type HumanTask = {
  id: string;
  execution_id: string;
  kind: 'review' | 'form';
  state: string;
  deadline_ms: number;
  escalated: boolean;
  escalate_at_ms: number | null;
  claim: 'none' | 'caller' | 'other';
  claim_expires_at_ms: number | null;
  intent_sha256: string | null;
};

export type Proposal = {
  intent_sha256: string;
  connector_name: string;
  connector_version: string;
  operation: string;
  policy_name: string;
  policy_version: string;
  approval_mode: 'none' | 'human';
  independent_approval: boolean;
  requester_id: string;
  issued_at_ms: number;
  expires_at_ms: number;
  target: unknown;
  input: unknown;
};

// What a person can do with a task now. Deciding needs the caller's own live claim.
export function taskActions(task: HumanTask, now = Date.now()) {
  if (task.state !== 'open' || task.deadline_ms <= now)
    return { claim: false, release: false, decide: false };
  // Another person's claim that has lapsed no longer holds the task.
  const lapsed = task.claim_expires_at_ms !== null && task.claim_expires_at_ms <= now;
  const mine = task.claim === 'caller' && !lapsed;
  return {
    claim: task.claim === 'none' || lapsed,
    release: mine,
    decide: mine,
  };
}

// The inbox order: escalated first, then the nearest deadline.
export function inboxOrder(tasks: readonly HumanTask[]) {
  return [...tasks].sort(
    (a, b) => Number(b.escalated) - Number(a.escalated) || a.deadline_ms - b.deadline_ms,
  );
}

// E06 (docs/superpowers/specs/2026-11-15-verified-outcomes-design.md): an execution's business
// outcome, as the server's one rule decides it. The console never recomputes it.
export type BusinessOutcome =
  'in_progress' | 'unknown' | 'not_verified' | 'verified' | 'completed' | 'partial' | 'no_effect';

export const outcomeWords: Record<BusinessOutcome, { label: string; tone: string; text: string }> =
  {
    in_progress: {
      label: 'In progress',
      tone: 'warning',
      text: 'It has not finished, so nothing is settled yet.',
    },
    unknown: {
      label: 'Unknown',
      tone: 'warning',
      text: 'Whether a write happened, or what it did, is not known. A person must settle it.',
    },
    not_verified: {
      label: 'Not verified',
      tone: 'danger',
      text: 'A write was made, and reading the system back did not confirm it.',
    },
    verified: {
      label: 'Verified',
      tone: 'success',
      text: 'It finished, and every write it made was read back and verified.',
    },
    completed: {
      label: 'Completed',
      tone: 'success',
      text: 'It finished without writing to any system.',
    },
    partial: {
      label: 'Partial',
      tone: 'warning',
      text: 'It stopped after some verified writes; the rest were not made.',
    },
    no_effect: {
      label: 'No effect',
      tone: 'neutral',
      text: 'It stopped before writing anything.',
    },
  };

export function outcomeOf(value: unknown) {
  return outcomeWords[value as BusinessOutcome] ?? outcomeWords.unknown;
}

export type TracePerson = { user_id: string; name: string | null } | null;

export type TraceAction = {
  action_id: string;
  step_id: string;
  connector: { name: string | null; version: string | null };
  operation: string | null;
  approval_mode: 'none' | 'human';
  authorization: string;
  effect: string;
  outcome: string;
  review: {
    task_id: string;
    state: string;
    decision: {
      decision: 'allow' | 'deny';
      decided_by: TracePerson;
      issued_at_ms: number;
    } | null;
  } | null;
  verification: { state: string; rule_id: string; observed_at_ms: number } | null;
};

export type Trace = {
  version: {
    package: string;
    package_version: string;
    package_sha256: string;
    revision: number;
    release_id: string | null;
  };
  actions: TraceAction[];
  outcome: { state: BusinessOutcome; exception: boolean };
  trace_sha256: string;
};

export type OutcomeSummary = {
  total: number;
  truncated: boolean;
  outcomes: Record<BusinessOutcome, number>;
  exceptions: number;
};

// Who allowed an action, in words: a decision names its person; no review is said plainly.
export function allowedBy(action: TraceAction) {
  if (action.approval_mode === 'none') return 'Allowed by its policy without review';
  const decision = action.review?.decision;
  if (!decision) return 'Waiting for a review';
  const who = decision.decided_by?.name || 'a reviewer';
  return `${decision.decision === 'allow' ? 'Allowed' : 'Denied'} by ${who}`;
}

// An action's effect state in words (E05's `effect_state`).
const effects: Record<string, string> = {
  not_dispatched: 'Not sent',
  dispatching: 'Being sent',
  accepted: 'Sent, and accepted by the system',
  observed: 'Sent, and seen in the system',
  unknown: 'Sent, with no certain answer',
  absent: 'Not made: reading the system back found no change',
};

export function effectWords(state: string) {
  return effects[state] ?? 'Unknown';
}

// E06, second part (docs/superpowers/specs/2026-11-15-cases-design.md): business cases.
export type CaseResolution =
  'corrected_by_hand' | 'handled_by_retry' | 'no_action_needed' | 'handled_elsewhere';

export const resolutionWords: Record<CaseResolution, string> = {
  corrected_by_hand: 'Corrected by hand in the business system',
  handled_by_retry: 'Handled by a later run',
  no_action_needed: 'No action needed',
  handled_elsewhere: 'Handled outside LotusPond',
};

export type CaseExecution = {
  id: string;
  state: string;
  admitted_at_ms: number;
  outcome: BusinessOutcome;
  exception: boolean;
};

export type BusinessCase = {
  id: string;
  key: string | null;
  key_sha256: string;
  created_at: string;
  outcome: BusinessOutcome | 'empty';
  needs_person: boolean;
  owner: (NonNullable<TracePerson> & { since_ms: number }) | null;
  resolution: {
    kind: CaseResolution;
    resolved_by: TracePerson;
    resolved_at_ms: number;
  } | null;
  execution_count: number;
  executions?: CaseExecution[];
};

// Cases that need a person first, unowned before owned, then the rest in their order.
export function caseOrder(cases: readonly BusinessCase[]) {
  const rank = (item: BusinessCase) => (item.needs_person ? (item.owner ? 1 : 0) : 2);
  return [...cases].sort((a, b) => rank(a) - rank(b));
}

// Control Room operations (docs/superpowers/specs/2026-11-16-control-room-operations-design.md).

// A worker not seen for two minutes is marked, never assumed gone: the server decides that.
export const SEEN_RECENTLY_MS = 120_000;

export function seenRecently(lastSeenMs: number | null, now = Date.now()) {
  return lastSeenMs !== null && now - lastSeenMs <= SEEN_RECENTLY_MS;
}

export type DeploymentStatus = 'enabled' | 'disabled' | 'revoked';

// The status changes a deployment offers. Revoked is final: nothing reopens it.
export function deploymentChanges(status: string): DeploymentStatus[] {
  if (status === 'enabled') return ['disabled', 'revoked'];
  if (status === 'disabled') return ['enabled', 'revoked'];
  return [];
}

// The command a runtime host runs with an issued enrollment token, saved to a private file.
export function enrollCommand(tokenFile = '/private/enrollment') {
  return `lotuspond-runtime enroll --config /private/runtime.json --token-file ${tokenFile}`;
}

// How full a queue limit is, for the meter: 0..1, and 0 when there is no limit.
export function fill(count: number, limit: number) {
  return limit > 0 ? Math.min(1, Math.max(0, count / limit)) : 0;
}

export type DependencyRef = { kind: string; name: string; version: string; sha256: string };

// A deployment's bindings: one per pinned dependency, as the server requires. A connector
// binds to a connection and a knowledge source to a source version; every other kind binds by
// its pin alone. A dependency reached only through another (an agent's model) is added by the
// author, as the server's answer names it.
export function bindingTemplate(refs: readonly DependencyRef[]) {
  return refs.map((ref) =>
    ref.kind === 'connector'
      ? { dependency: ref, connection_id: '<connection id>', connection_version: 1 }
      : ref.kind === 'knowledge'
        ? { dependency: ref, source_id: '<source id>', source_version: 1 }
        : { dependency: ref },
  );
}
