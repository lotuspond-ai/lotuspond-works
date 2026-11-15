import assert from 'node:assert/strict';
import test from 'node:test';
import {
  allowedControls,
  fromMs,
  inboxOrder,
  opaqueId,
  operateMessage,
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
