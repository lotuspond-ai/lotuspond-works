import assert from 'node:assert/strict';
import test from 'node:test';
import { environmentLabel } from './environmentLabel.js';

test('a non-production server shows the label it sets', () => {
  assert.equal(environmentLabel({ status: 'ok', environment_label: 'Staging' }), 'Staging');
});

test('production, which leaves the label unset, shows nothing', () => {
  assert.equal(environmentLabel({ status: 'ok', environment_label: null }), null);
  assert.equal(environmentLabel({ status: 'ok' }), null);
  assert.equal(environmentLabel(null), null);
});

test('a value the server would never send is ignored', () => {
  assert.equal(environmentLabel({ environment_label: '   ' }), null);
  assert.equal(environmentLabel({ environment_label: 42 }), null);
  assert.equal(environmentLabel({ environment_label: 'x'.repeat(25) }), null);
});
