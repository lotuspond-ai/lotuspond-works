import assert from 'node:assert/strict';
import test from 'node:test';
import {
  V2_PATTERNS,
  clone,
  isV2,
  matchDependencies,
  moveStep,
  newStep,
  pinnedRefs,
  problemsByStep,
  signCommand,
  stepLabel,
  uniqueStepId,
  type Step,
} from './studioV2.js';

test('the patterns are the SDK examples, each a v2 package with a distinct id', () => {
  assert.equal(V2_PATTERNS.length, 5);
  for (const pattern of V2_PATTERNS) assert.ok(isV2(pattern.package), pattern.id);
  const ids = V2_PATTERNS.map((pattern) => pattern.package.package_id);
  assert.equal(new Set(ids).size, ids.length);
  const reviewed = V2_PATTERNS.find((pattern) => pattern.id === 'reviewed_action')!;
  assert.deepEqual(
    reviewed.package.body.steps.map((step) => step.kind),
    ['action_intent', 'human_task', 'action_dispatch', 'outcome_verification'],
  );
});

test('a v1 package or anything else is not a v2 package', () => {
  assert.equal(
    isV2({ schema_version: 'lotuspond.workflow-package.v1', body: { steps: [] } }),
    false,
  );
  assert.equal(isV2(null), false);
  assert.equal(isV2([]), false);
  assert.equal(isV2({ schema_version: 'lotuspond.workflow-package.v2', body: {} }), false);
});

test('steps move within bounds, and new ids never collide', () => {
  const steps = ['a', 'b', 'c'].map((id) => ({ kind: 'timer', id }) as Step);
  assert.deepEqual(
    moveStep(steps, 0, 2).map((s) => s.id),
    ['b', 'c', 'a'],
  );
  assert.deepEqual(
    moveStep(steps, 2, 0).map((s) => s.id),
    ['c', 'a', 'b'],
  );
  assert.deepEqual(
    moveStep(steps, 0, 5).map((s) => s.id),
    ['a', 'b', 'c'],
  );
  assert.equal(uniqueStepId(steps, 'read'), 'read');
  assert.equal(uniqueStepId(steps, 'a'), 'a_2');
});

test('new steps are shaped as the SDK step models', () => {
  const read = newStep('connector_read', 'read');
  assert.deepEqual(Object.keys(read).sort(), [
    'connector_ref',
    'id',
    'input',
    'kind',
    'operation',
    'retry',
    'target',
  ]);
  assert.equal(stepLabel(newStep('agent', 'think')), 'Agent');
  assert.equal(newStep('timer', 't').delay_seconds, 60);
});

test('problems attach to the step they point into; the rest stay with the package', () => {
  const { steps, rest } = problemsByStep([
    { path: '/body/steps/2/*/*', code: 'invalid_package', message: 'm' },
    { path: '/body/steps/2', code: 'unknown_reference', message: 'm' },
    { path: '/limits/max_steps', code: 'invalid_package', message: 'm' },
    { path: '', code: 'dependency_missing', message: 'm' },
  ]);
  assert.equal(steps.get(2)?.length, 2);
  assert.equal(rest.length, 2);
});

test('pins match publications exactly, and anything else is missing', () => {
  const pkg = clone(V2_PATTERNS.find((p) => p.id === 'reviewed_action')!.package);
  const refs = pinnedRefs(pkg);
  assert.deepEqual(refs.map((ref) => `${ref.kind}:${ref.name}`).sort(), [
    'connector:connector-fixture',
    'policy:policy-fixture',
  ]);
  const connector = refs.find((ref) => ref.kind === 'connector')!;
  const exact = {
    id: 'pub-1',
    kind: 'connector',
    name: connector.name,
    version: connector.version,
    package_sha256: connector.sha256,
  };
  const otherDigest = { ...exact, id: 'pub-2', package_sha256: '0'.repeat(64) };
  const result = matchDependencies(refs, [otherDigest, exact]);
  assert.deepEqual(result.ids, ['pub-1']);
  assert.deepEqual(
    result.missing.map((ref) => ref.kind),
    ['policy'],
  );
});

test('the signing command names the SDK and never a key value', () => {
  assert.match(
    signCommand(),
    /^lotuspond-package v2 sign package\.json --key <publisher-key\.pem>/,
  );
});
