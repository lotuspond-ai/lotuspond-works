import { spawn } from 'node:child_process';
import { dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');

export const COMMANDS = {
  'runner-http': {
    script: resolve(root, 'scripts/runner.mjs'),
    args: [resolve(root, 'protocol-adapters/src/http-runner.mjs')],
    summary: 'Supervised HTTP agent adapter. Set LOTUSPOND_RUNNER_ID, LOTUSPOND_API_URL, and LOTUSPOND_API_KEY.',
  },
  'runner-mcp': {
    script: resolve(root, 'scripts/runner.mjs'),
    args: [resolve(root, 'protocol-adapters/src/mcp-runner.mjs')],
    summary: 'Supervised MCP tool adapter. Set LOTUSPOND_RUNNER_ID, LOTUSPOND_API_URL, and LOTUSPOND_API_KEY.',
  },
  'runner-a2a': {
    script: resolve(root, 'scripts/runner.mjs'),
    args: [resolve(root, 'protocol-adapters/src/a2a-runner.mjs')],
    summary: 'Supervised A2A agent adapter. Set LOTUSPOND_RUNNER_ID, LOTUSPOND_API_URL, and LOTUSPOND_API_KEY.',
  },
  voice: {
    script: resolve(root, 'voice-bridge/src/cli.mjs'),
    args: [],
    summary: 'PCM transcription bridge. Airgap mode refuses provider traffic before opening a socket.',
  },
  gate: {
    script: resolve(root, 'scripts/cli.mjs'),
    prefix: ['gate'],
    summary: 'Evaluate a release: gate RELEASE_ID',
  },
};

export function usage() {
  return [
    'Lotuspond execution commands',
    '',
    'LOTUSPOND_API_URL may be an origin (http://127.0.0.1:8000) or the API base ending in /api/v1.',
    'Remote URLs must use HTTPS. This image has no Docker socket, Python runtime, or baked credentials.',
    '',
    'Commands:',
    '  runner-http   ' + COMMANDS['runner-http'].summary,
    '  runner-mcp    ' + COMMANDS['runner-mcp'].summary,
    '  runner-a2a    ' + COMMANDS['runner-a2a'].summary,
    '  voice         ' + COMMANDS.voice.summary,
    '  gate          ' + COMMANDS.gate.summary,
    '',
    'Gym tasks use the separate host-side kit from the execution archive, not this image.',
  ].join('\n');
}

export function resolveCommand(argv) {
  const command = argv[2];
  if (!command || command === 'help' || command === '--help') return { kind: 'help' };
  const spec = COMMANDS[command];
  if (!spec) return { kind: 'unknown', command };
  return {
    kind: 'run',
    command,
    script: spec.script,
    args: [...(spec.prefix || spec.args || []), ...argv.slice(3)],
  };
}

function delegate(script, args) {
  const child = spawn(process.execPath, [script, ...args], { stdio: 'inherit' });
  const stop = (signal) => {
    try {
      child.kill(signal);
    } catch {}
  };
  process.on('SIGTERM', stop);
  process.on('SIGINT', stop);
  child.on('exit', (code, signal) => {
    process.off('SIGTERM', stop);
    process.off('SIGINT', stop);
    if (signal) {
      process.kill(process.pid, signal);
      return;
    }
    process.exit(code ?? 1);
  });
}

const invoked = process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href;
if (invoked) {
  const resolved = resolveCommand(process.argv);
  if (resolved.kind === 'help') {
    console.log(usage());
  } else if (resolved.kind === 'unknown') {
    console.error(`Unknown command ${resolved.command}.`);
    console.error(usage());
    process.exitCode = 2;
  } else delegate(resolved.script, resolved.args);
}
