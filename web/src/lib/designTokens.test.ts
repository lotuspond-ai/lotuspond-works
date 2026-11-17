import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

// The console names its tokens for what they do in the application (console.css). Each one is
// a token of the LotusPond design system (a copy of design-system/project/tokens.json from
// lotuspond-ai/lotuspond, kept in ../styles/lotuspond-tokens.json). This test fails
// when a value is changed in one place and not the other.
const css = readFileSync(new URL('../styles/console.css', import.meta.url), 'utf8');
const system = JSON.parse(
  readFileSync(new URL('../styles/lotuspond-tokens.json', import.meta.url), 'utf8'),
) as { color: { tokens: { name: string; value: Record<string, string> }[] } };

const light = new Map(system.color.tokens.map((token) => [token.name, token.value.light]));
function resolve(name: string, depth = 0): string {
  const value = light.get(name);
  assert.ok(value, `${name} is a design system colour`);
  assert.ok(depth < 8, `${name} resolves without a cycle`);
  return value.startsWith('{') ? resolve(value.slice(1, -1), depth + 1) : value.toLowerCase();
}

const start = css.indexOf(':root,\n.console-window {');
assert.notEqual(start, -1, 'the console token block is declared');
const block = css.slice(start, css.indexOf('}', start));
const consoleTokens = Object.fromEntries(
  [...block.matchAll(/(--[\w-]+):\s*(#[0-9a-f]{6})\b/gi)].map(([, name, value]) => [
    name,
    value.toLowerCase(),
  ]),
);

const mapping: [string, string][] = [
  ['--canvas', 'surface-tint'],
  ['--paper', 'surface-raised'],
  ['--sunken', 'surface'],
  ['--ink', 'ink'],
  ['--ink-mark', 'ink'],
  ['--muted', 'ink-muted'],
  ['--line', 'line'],
  ['--field-line', 'line-strong'],
  ['--focus', 'focus'],
  ['--accent', 'brand-text'],
  ['--success', 'positive'],
  ['--success-soft', 'positive-tint'],
  ['--success-line', 'sage-300'],
  ['--warning', 'caution'],
  ['--danger', 'danger'],
  ['--lotus', 'accent-mark'],
  ['--decide', 'accent'],
  ['--decide-soft', 'accent-tint'],
];

test('every mapped console token equals its design system token', () => {
  for (const [consoleName, systemName] of mapping) {
    assert.equal(
      consoleTokens[consoleName],
      resolve(systemName),
      `${consoleName} should equal ${systemName}`,
    );
  }
});

test('the design system declares a light and a dark value for every colour', () => {
  for (const token of system.color.tokens) {
    assert.ok(token.value.light, `${token.name} has a light value`);
    assert.ok(token.value.dark, `${token.name} has a dark value`);
  }
});
