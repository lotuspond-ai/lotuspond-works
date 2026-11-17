import assert from 'node:assert/strict';
import test from 'node:test';
import {
  allowedBy,
  allowedControls,
  bindingTemplate,
  deploymentChanges,
  enrollCommand,
  fill,
  seenRecently,
  caseOrder,
  effectWords,
  fromMs,
  inboxOrder,
  opaqueId,
  decisionMessage,
  operateMessage,
  outcomeOf,
  scopedPath,
  taskActions,
  type HumanTask,
} from './operate.js';

const task = (overrides: Partial<HumanTask> = {}): HumanTask => ({
  id: 't1',
  execution_id: 'x1',
  kind: 'review',
  state: 'open',
  deadline_ms: 10_000,
  escalated: false,
  escalate_at_ms: null,
  claim: 'none',
  claim_expires_at_ms: null,
  intent_sha256: 'a'.repeat(64),
  ...overrides,
});

test('execution paths are scoped to one project and environment, encoded', () => {
  assert.equal(
    scopedPath({ projectId: 'p 1', environmentId: 'e/1' }, 'human-tasks'),
    '/projects/p%201/environments/e%2F1/human-tasks',
  );
});

test('epoch milliseconds become ISO text, anything else nothing', () => {
  assert.equal(fromMs(0), '1970-01-01T00:00:00.000Z');
  assert.equal(fromMs(null), undefined);
  assert.equal(fromMs('5'), undefined);
});

test('request identities satisfy the OpaqueId pattern', () => {
  const id = opaqueId('decision');
  assert.match(id, /^[A-Za-z0-9][A-Za-z0-9._:-]*$/);
  assert.ok(id.length <= 128);
  assert.equal(
    opaqueId('x', () => 'fixed'),
    'x-fixed',
  );
});

test('closed codes become sentences and unknown ones keep the fallback', () => {
  assert.match(operateMessage('authority_unavailable', 'x'), /not switched on/);
  assert.equal(operateMessage('something_new', 'fallback'), 'fallback');
  assert.equal(operateMessage(undefined, 'fallback'), 'fallback');
});

test('controls follow the observed and requested state', () => {
  assert.deepEqual(allowedControls('running', 'running'), ['pause', 'cancel']);
  assert.deepEqual(allowedControls('waiting', 'running'), ['pause', 'cancel']);
  assert.deepEqual(allowedControls('paused', 'paused'), ['resume', 'cancel']);
  // Pause requested but not yet observed: resume withdraws it.
  assert.deepEqual(allowedControls('running', 'paused'), ['resume', 'cancel']);
  assert.deepEqual(allowedControls('running', 'cancelled'), []);
  for (const finished of ['succeeded', 'failed', 'cancelled'])
    assert.deepEqual(allowedControls(finished, finished), []);
});

test('only the caller’s live claim can decide; a lapsed claim can be taken', () => {
  const now = 1_000;
  assert.deepEqual(taskActions(task(), now), { claim: true, release: false, decide: false });
  assert.deepEqual(taskActions(task({ claim: 'caller', claim_expires_at_ms: 5_000 }), now), {
    claim: false,
    release: true,
    decide: true,
  });
  assert.deepEqual(taskActions(task({ claim: 'other', claim_expires_at_ms: 5_000 }), now), {
    claim: false,
    release: false,
    decide: false,
  });
  assert.deepEqual(taskActions(task({ claim: 'other', claim_expires_at_ms: 500 }), now), {
    claim: true,
    release: false,
    decide: false,
  });
  assert.deepEqual(taskActions(task({ claim: 'caller', claim_expires_at_ms: 500 }), now), {
    claim: true,
    release: false,
    decide: false,
  });
});

test('a closed or past-deadline task offers nothing', () => {
  const none = { claim: false, release: false, decide: false };
  assert.deepEqual(taskActions(task({ state: 'decided' }), 0), none);
  assert.deepEqual(taskActions(task({ deadline_ms: 500 }), 1_000), none);
});

test('the inbox puts escalated tasks first, then the nearest deadline', () => {
  const ordered = inboxOrder([
    task({ id: 'late', deadline_ms: 9_000 }),
    task({ id: 'soon', deadline_ms: 2_000 }),
    task({ id: 'escalated', deadline_ms: 8_000, escalated: true }),
  ]);
  assert.deepEqual(
    ordered.map((item) => item.id),
    ['escalated', 'soon', 'late'],
  );
});

test('every business outcome has words, and an unexpected one reads as unknown', () => {
  for (const state of [
    'in_progress',
    'unknown',
    'not_verified',
    'verified',
    'completed',
    'partial',
    'no_effect',
  ])
    assert.ok(outcomeOf(state).label && outcomeOf(state).text, state);
  assert.equal(outcomeOf('new_state').label, 'Unknown');
});

test('who allowed an action is named, never assumed', () => {
  const base = {
    action_id: 'a',
    step_id: 's',
    connector: { name: 'erp', version: '1' },
    operation: 'update',
    authorization: 'approved',
    effect: 'observed',
    outcome: 'verified',
    verification: null,
  };
  assert.equal(
    allowedBy({ ...base, approval_mode: 'none', review: null }),
    'Allowed by its policy without review',
  );
  assert.equal(
    allowedBy({
      ...base,
      approval_mode: 'human',
      review: { task_id: 't', state: 'open', decision: null },
    }),
    'Waiting for a review',
  );
  const decided = (decision: 'allow' | 'deny', name: string | null) => ({
    ...base,
    approval_mode: 'human' as const,
    review: {
      task_id: 't',
      state: 'decided',
      decision: { decision, decided_by: { user_id: 'u', name }, issued_at_ms: 1 },
    },
  });
  assert.equal(allowedBy(decided('allow', 'Asha')), 'Allowed by Asha');
  assert.equal(allowedBy(decided('deny', null)), 'Denied by a reviewer');
});

test('effect states read as words, and an unexpected one as unknown', () => {
  assert.equal(effectWords('observed'), 'Sent, and seen in the system');
  assert.equal(effectWords('something'), 'Unknown');
});

test('cases that need a person come first, unowned before owned', () => {
  const item = (id: string, needs: boolean, owned: boolean) =>
    ({
      id,
      key: id,
      key_sha256: 'x',
      created_at: '',
      outcome: 'verified',
      needs_person: needs,
      owner: owned ? { user_id: 'u', name: 'A', since_ms: 1 } : null,
      resolution: null,
      execution_count: 1,
    }) as const;
  const ordered = caseOrder([
    item('fine', false, false),
    item('owned', true, true),
    item('open', true, false),
  ]);
  assert.deepEqual(
    ordered.map((c) => c.id),
    ['open', 'owned', 'fine'],
  );
});

test('a worker is marked when not seen for two minutes', () => {
  assert.equal(seenRecently(1_000, 1_000 + 120_000), true);
  assert.equal(seenRecently(1_000, 1_000 + 120_001), false);
  assert.equal(seenRecently(null, 0), false);
});

test('deployments change status, and revoked is final', () => {
  assert.deepEqual(deploymentChanges('enabled'), ['disabled', 'revoked']);
  assert.deepEqual(deploymentChanges('disabled'), ['enabled', 'revoked']);
  assert.deepEqual(deploymentChanges('revoked'), []);
});

test('queue meters stay within bounds', () => {
  assert.equal(fill(3, 5), 0.6);
  assert.equal(fill(9, 5), 1);
  assert.equal(fill(1, 0), 0);
});

test('every pin gets a binding; connectors and knowledge name what they bind to', () => {
  const ref = (kind: string) => ({ kind, name: kind, version: '1.0.0', sha256: 'a'.repeat(64) });
  const template = bindingTemplate([ref('connector'), ref('model'), ref('knowledge')]);
  assert.deepEqual(
    template.map((item) => Object.keys(item).sort().join(',')),
    [
      'connection_id,connection_version,dependency',
      'dependency',
      'dependency,source_id,source_version',
    ],
  );
  assert.equal(enrollCommand().startsWith('lotuspond-runtime enroll --config'), true);
});

test('a refused Allow on an independent review names the reason, not the session', () => {
  assert.match(decisionMessage('forbidden', 'allow', true, 'x'), /independent approval/);
  // Any other refusal keeps the operate message.
  assert.equal(decisionMessage('forbidden', 'allow', false, 'x'), operateMessage('forbidden', 'x'));
  assert.equal(decisionMessage('forbidden', 'deny', true, 'x'), operateMessage('forbidden', 'x'));
  assert.equal(decisionMessage('conflict', 'allow', true, 'x'), operateMessage('conflict', 'x'));
});
