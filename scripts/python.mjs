import { existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { spawn } from 'node:child_process';

const python = resolve('backend/.venv/bin/python');
if (process.argv[2] === 'test-database' && !process.env.LOTUSPOND_PLATFORM_TEST_DATABASE_URL)
  throw new Error(
    'Set LOTUSPOND_PLATFORM_TEST_DATABASE_URL to the isolated localhost lotuspond_platform_test database.',
  );
if (!existsSync(python))
  throw new Error('Install the Python environment with: cd backend && uv sync --frozen');
const commands = {
  dev: ['-m', 'uvicorn', 'lotuspond.app:app', '--host', '127.0.0.1', '--port', '8000', '--reload'],
  start: [
    '-m',
    'uvicorn',
    'lotuspond.app:app',
    '--host',
    '127.0.0.1',
    '--port',
    '8000',
    '--no-server-header',
  ],
  worker: ['-m', 'lotuspond.worker'],
  'mail-worker': ['-m', 'lotuspond.mail_worker'],
  'alert-worker': ['-m', 'lotuspond.alert_worker'],
  init: ['-m', 'lotuspond.initialize', 'init'],
  migrate: ['-m', 'lotuspond.initialize', 'migrate'],
  'offline-identity': ['-m', 'lotuspond.offline_provision'],
  retention: ['-m', 'lotuspond.retention'],
  package: ['-m', 'lotuspond.package_cli'],
  'package-schema': ['export_package_schema.py'],
  test: ['-m', 'pytest', '-q'],
  'test-database': ['-m', 'pytest', '-q'],
  lint: ['-m', 'ruff', 'check', 'lotuspond', 'tests', 'export_package_schema.py'],
};
const command = commands[process.argv[2]];
if (!command)
  throw new Error(
    'Select dev, start, worker, mail-worker, alert-worker, init, migrate, offline-identity, retention, package, package-schema, test, test-database, or lint.',
  );
const child = spawn(python, [...command, ...process.argv.slice(3)], {
  // Package paths are relative to the caller, like other file-oriented CLIs.
  cwd: process.argv[2] === 'package' ? process.cwd() : resolve('backend'),
  stdio: 'inherit',
  env: process.env,
});
for (const signal of ['SIGTERM', 'SIGINT']) process.on(signal, () => child.kill(signal));
child.on('exit', (code) => {
  process.exitCode = code ?? 1;
});
