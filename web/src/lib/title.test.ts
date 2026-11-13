import assert from 'node:assert/strict';
import test from 'node:test';
import { documentTitle } from './title.js';

test('a view title leads with the most specific part and ends with the product', () => {
  assert.equal(
    documentTitle('Releases', 'Example organization'),
    'Releases · Example organization · LotusPond Works',
  );
  assert.equal(documentTitle('Sign in'), 'Sign in · LotusPond Works');
});

test('missing or blank parts are left out rather than shown as gaps', () => {
  assert.equal(documentTitle(), 'LotusPond Works');
  assert.equal(documentTitle(undefined, 'Personal'), 'Personal · LotusPond Works');
  assert.equal(documentTitle('  ', 'Home'), 'Home · LotusPond Works');
});
