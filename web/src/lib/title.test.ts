import assert from 'node:assert/strict';
import test from 'node:test';
import { documentTitle } from './title.js';

test('a view title leads with the most specific part and ends with the product', () => {
  assert.equal(
    documentTitle('Releases', 'Example organization'),
    'Releases · Example organization · Lotuspond',
  );
  assert.equal(documentTitle('Sign in'), 'Sign in · Lotuspond');
});

test('missing or blank parts are left out rather than shown as gaps', () => {
  assert.equal(documentTitle(), 'Lotuspond');
  assert.equal(documentTitle(undefined, 'Personal'), 'Personal · Lotuspond');
  assert.equal(documentTitle('  ', 'Home'), 'Home · Lotuspond');
});
