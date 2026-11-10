import assert from 'node:assert/strict';
import test from 'node:test';
import { resolve } from 'node:path';
import { COMMANDS, resolveCommand, usage } from './execution-cli.mjs';

test('execution CLI documents the four packaged commands and API URL convention', () => {
  const text = usage();
  for (const command of ['runner-http', 'runner-mcp', 'runner-a2a', 'gate'])
    assert.match(text, new RegExp(`\\b${command}\\b`));
  assert.match(text, /\/api\/v1/);
  assert.match(text, /no Docker socket/);
  assert.doesNotMatch(text, /docker\.sock/);
});

test('execution CLI maps runner adapters without a repository path from the caller', () => {
  const http = resolveCommand(['node', 'execution-cli.mjs', 'runner-http']);
  assert.equal(http.kind, 'run');
  assert.equal(http.script, COMMANDS['runner-http'].script);
  assert.equal(http.args[0], resolve('protocol-adapters/src/http-runner.mjs'));
  const a2a = resolveCommand(['node', 'execution-cli.mjs', 'runner-a2a']);
  assert.equal(a2a.script, COMMANDS['runner-a2a'].script);
  assert.match(a2a.args[0], /a2a-runner\.mjs$/);
  const mcp = resolveCommand(['node', 'execution-cli.mjs', 'runner-mcp']);
  assert.equal(mcp.args[0], resolve('protocol-adapters/src/mcp-runner.mjs'));
  const gate = resolveCommand(['node', 'execution-cli.mjs', 'gate', 'release-id']);
  assert.deepEqual(gate.args, ['gate', 'release-id']);
  assert.equal(resolveCommand(['node', 'execution-cli.mjs']).kind, 'help');
  assert.equal(resolveCommand(['node', 'execution-cli.mjs', 'gym']).kind, 'unknown');
  assert.equal(resolveCommand(['node', 'execution-cli.mjs', 'voice']).kind, 'unknown');
});
