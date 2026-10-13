import assert from 'node:assert/strict';
import test from 'node:test';
import { setupSteps, summarize, takeCreateRequest } from './setup.js';

test('the setup path leads to a release decision and counts only finished steps', () => {
  assert.equal(setupSteps.at(-1)?.id, 'releases');
  assert.equal(summarize({}), 0);
  assert.equal(summarize({ projects: 'done', scenarios: 'todo', runners: 'unknown' }), 1);
  assert.equal(
    summarize(Object.fromEntries(setupSteps.map((step) => [step.id, 'done']))),
    setupSteps.length,
  );
});

test('an unrequested create form stays closed', () => {
  assert.equal(takeCreateRequest('projects'), false);
});
