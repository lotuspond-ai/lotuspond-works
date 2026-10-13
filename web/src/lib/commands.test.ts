import assert from 'node:assert/strict';
import test from 'node:test';
import { rank } from './commands.js';

const commands = [
  { label: 'Evaluation runs', section: 'Go to' },
  { label: 'Releases', section: 'Go to' },
  { label: 'Create release', section: 'Create' },
  { label: 'Policies', section: 'Go to' },
];

test('an empty query keeps the navigation order', () => {
  assert.deepEqual(
    rank(commands, '  ').map((command) => command.label),
    commands.map((command) => command.label),
  );
});

test('prefix matches rank before word matches, which rank before substrings', () => {
  assert.deepEqual(
    rank(commands, 'rel').map((command) => command.label),
    ['Releases', 'Create release'],
  );
  assert.deepEqual(
    rank(commands, 'run').map((command) => command.label),
    ['Evaluation runs'],
  );
});

test('a section name finds its commands, and nothing matches nonsense', () => {
  assert.deepEqual(
    rank(commands, 'create').map((command) => command.label),
    ['Create release'],
  );
  assert.deepEqual(rank(commands, 'zzz'), []);
});
