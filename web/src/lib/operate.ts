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
