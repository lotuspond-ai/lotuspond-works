import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { realpathSync } from 'node:fs';
import { mkdtemp, readFile, rm, symlink, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import test from 'node:test';
import { confinedAdapterPath } from './runner.mjs';
import {
  adapterEnvironment,
  executeScenario,
  renewalInterval,
  scenarioTimeout,
  startLeaseRenewal,
} from './runner-execution.mjs';
import { boundedScenarioResult, boundedSubmissionResults } from './runner.mjs';

const cleanupPaths = [];
test.afterEach(async () => {
  await Promise.all(
    cleanupPaths.splice(0).map((path) => rm(path, { force: true, recursive: true })),
  );
});

function waitForChild(child, timeoutMs = 2000) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      child.kill('SIGKILL');
      reject(new Error('supervisor did not exit within its bound'));
    }, timeoutMs);
    child.once('error', reject);
    child.once('close', (code) => {
      clearTimeout(timer);
      resolve(code);
    });
  });
}

async function fixture(source) {
  const directory = await mkdtemp(join(tmpdir(), 'lotuspond-runner-'));
  cleanupPaths.push(directory);
  const path = join(directory, 'adapter.mjs');
  await writeFile(path, source, { mode: 0o600 });
  return { directory, path };
}

const base = (path, extra = {}) => ({
  adapterPath: path,
  input: { prompt: 'bounded' },
  expected: { value: true },
  revision: 3,
  runId: 'run-1',
  scenarioId: 'scenario-1',
  timeoutMs: 2000,
  cleanupGraceMs: 100,
  environment: { PATH: process.env.PATH, TMPDIR: process.env.TMPDIR },
  ...extra,
});

test('successful adapter receives bounded scenario context', async () => {
  const { path } = await fixture(`export async function evaluate(input, expected, context) {
    return { passed: true, output: { input, expected, revision: context.revision, runId: context.runId, scenarioId: context.scenarioId } };
  }`);
  const result = await executeScenario(base(path));
  assert.equal(result.classification, 'success');
  assert.deepEqual(result.result.output, {
    input: { prompt: 'bounded' },
    expected: { value: true },
    revision: 3,
    runId: 'run-1',
    scenarioId: 'scenario-1',
  });
  assert.equal(result.processGroupGone, true);
});

test('CPU-bound adapter is forcibly stopped at the wall deadline', async () => {
  const { path } = await fixture('export function evaluate() { while (true) {} }');
  const result = await executeScenario(base(path, { timeoutMs: 150 }));
  assert.equal(result.classification, 'timeout');
  assert.equal(result.processGroupGone, true);
  assert.ok(result.latencyMs < 1500);
});

test('abort stops an adapter that ignores its AbortSignal', async () => {
  const { path } = await fixture(
    'export async function evaluate() { await new Promise(() => setInterval(() => {}, 1000)); }',
  );
  const controller = new AbortController();
  setTimeout(() => controller.abort(), 80);
  const result = await executeScenario(base(path, { signal: controller.signal }));
  assert.equal(result.classification, 'canceled');
  assert.equal(result.processGroupGone, true);
});

test('cooperative abort lets the adapter finish its cleanup before group termination', async () => {
  const { path } = await fixture(`import { writeFileSync } from 'node:fs';
    export async function evaluate(_input, _expected, context) {
      await new Promise((resolve) => context.signal.addEventListener('abort', () => {
        writeFileSync(process.env.CUSTOM_CLEANUP_FILE, 'clean');
        clearInterval(keepAlive);
        resolve();
      }, { once: true }));
      return { passed: null, output: { stopped: true } };
    }
    const keepAlive = setInterval(() => {}, 1000);`);
  const cleanupFile = join(tmpdir(), `lotuspond-cleanup-${process.pid}-${Date.now()}`);
  cleanupPaths.push(cleanupFile);
  const controller = new AbortController();
  setTimeout(() => controller.abort(), 80);
  const result = await executeScenario(
    base(path, {
      signal: controller.signal,
      cleanupGraceMs: 500,
      environment: {
        PATH: process.env.PATH,
        TMPDIR: process.env.TMPDIR,
        LOTUSPOND_RUNNER_ADAPTER_ENV: 'CUSTOM_CLEANUP_FILE',
        CUSTOM_CLEANUP_FILE: cleanupFile,
      },
    }),
  );
  assert.equal(result.classification, 'canceled');
  assert.equal(await readFile(cleanupFile, 'utf8'), 'clean');
});

test('early cooperative exit cancels the production cleanup-grace timer', async (t) => {
  const { directory, path } = await fixture(`import { existsSync, writeFileSync } from 'node:fs';
    // Exercise startup slower than the former fixed 50ms cancellation delay.
    await new Promise((resolve) => setTimeout(resolve, 150));
    export async function evaluate(_input, _expected, context) {
      const keepAlive = setInterval(() => {}, 1000);
      const stopped = new Promise((resolve) => {
        context.signal.addEventListener('abort', () => {
          writeFileSync(process.env.CUSTOM_CLEANUP_FILE, 'clean');
          clearInterval(keepAlive); resolve();
        }, { once: true });
      });
      // Force create -> parent read -> complete publication across processes.
      writeFileSync(process.env.CUSTOM_READY_FILE, '');
      const publicationDeadline = Date.now() + 1000;
      while (!existsSync(process.env.CUSTOM_OBSERVED_FILE)) {
        if (Date.now() >= publicationDeadline) throw new Error('readiness was not observed');
        await new Promise((resolve) => setTimeout(resolve, 5));
      }
      await new Promise((resolve) => setTimeout(resolve, 25));
      writeFileSync(process.env.CUSTOM_READY_FILE, 'ready');
      await stopped;
      return { passed: null, output: {} };
    }`);
  const readyFile = join(directory, 'ready');
  const observedFile = join(directory, 'observed-incomplete-ready');
  const cleanupFile = join(directory, 'cleanup');
  const pidFile = join(directory, 'adapter-pid');
  const resultFile = join(directory, 'result.json');
  const moduleUrl = new URL('./runner-execution.mjs', import.meta.url).href;
  const program = `import childProcess from 'node:child_process';
    import { writeFileSync } from 'node:fs';
    import { syncBuiltinESMExports } from 'node:module';
    // Record ownership at spawn, including failure before the fixture can start.
    const originalSpawn = childProcess.spawn;
    childProcess.spawn = (...args) => {
      const adapter = originalSpawn(...args);
      if (adapter.pid) writeFileSync(${JSON.stringify(pidFile)}, String(adapter.pid));
      return adapter;
    };
    syncBuiltinESMExports();
    const { executeScenario } = await import(${JSON.stringify(moduleUrl)});
    const controller = new AbortController();
    process.once('message', () => controller.abort());
    const result = await executeScenario({ adapterPath: ${JSON.stringify(path)}, input: {}, expected: {},
      revision: 1, runId: 'run', scenarioId: 'scenario', signal: controller.signal,
      environment: { PATH: process.env.PATH, TMPDIR: process.env.TMPDIR,
        LOTUSPOND_RUNNER_ADAPTER_ENV: 'CUSTOM_READY_FILE,CUSTOM_CLEANUP_FILE,CUSTOM_OBSERVED_FILE',
        CUSTOM_READY_FILE: ${JSON.stringify(readyFile)},
        CUSTOM_OBSERVED_FILE: ${JSON.stringify(observedFile)},
        CUSTOM_CLEANUP_FILE: ${JSON.stringify(cleanupFile)} } });
    writeFileSync(${JSON.stringify(resultFile)}, JSON.stringify(result));
    process.disconnect();`;
  const child = spawn(process.execPath, ['--input-type=module', '-e', program], {
    detached: true,
    stdio: ['ignore', 'ignore', 'ignore', 'ipc'],
  });
  const closed = new Promise((resolve) => child.once('close', resolve));
  let spawnError;
  child.on('error', (error) => {
    spawnError = error;
  });
  const groupExists = (pid) => {
    try {
      process.kill(-pid, 0);
      return true;
    } catch (error) {
      if (error.code === 'ESRCH') return false;
      throw error;
    }
  };
  try {
    const startupDeadline = performance.now() + 2000;
    let incompleteReadyReads = 0;
    while (true) {
      try {
        const content = await readFile(readyFile, 'utf8');
        if (content === 'ready') break;
        if (content === '') {
          incompleteReadyReads += 1;
          if (incompleteReadyReads === 1) await writeFile(observedFile, 'observed');
        }
      } catch (error) {
        if (error.code !== 'ENOENT') throw error;
      }
      if (spawnError) throw spawnError;
      assert.equal(child.exitCode, null, 'supervisor exited before adapter readiness');
      assert.ok(performance.now() < startupDeadline, 'adapter readiness exceeded its bound');
      await new Promise((resolve) => setTimeout(resolve, 5));
    }
    assert.ok(incompleteReadyReads > 0, 'fixture did not exercise incomplete publication');
    const started = performance.now();
    const exited = waitForChild(child);
    child.send('abort');
    assert.equal(await exited, 0);
    assert.ok(performance.now() - started < 1500);
    assert.equal(await readFile(cleanupFile, 'utf8'), 'clean');
    const result = JSON.parse(await readFile(resultFile, 'utf8'));
    assert.equal(result.classification, 'canceled');
    assert.equal(result.processGroupGone, true);
    t.diagnostic(JSON.stringify({ incompleteReadyReads, result, cleanupMarker: 'clean' }));
    const adapterPid = Number(await readFile(pidFile, 'utf8'));
    assert.equal(groupExists(adapterPid), false);
  } finally {
    const groups = [child.pid];
    try {
      const adapterPid = Number(await readFile(pidFile, 'utf8'));
      assert.ok(Number.isInteger(adapterPid) && adapterPid > 0);
      groups.push(adapterPid);
    } catch (error) {
      if (error.code !== 'ENOENT') throw error;
    }
    for (const pid of groups) {
      try {
        process.kill(-pid, 'SIGKILL');
      } catch (error) {
        if (error.code !== 'ESRCH') throw error;
      }
    }
    let closeTimer;
    try {
      await Promise.race([
        closed,
        new Promise((_, reject) => {
          closeTimer = setTimeout(() => reject(new Error('owned supervisor remains')), 1000);
        }),
      ]);
    } finally {
      clearTimeout(closeTimer);
    }
    const cleanupDeadline = performance.now() + 1000;
    while (groups.some(groupExists) && performance.now() < cleanupDeadline) {
      await new Promise((resolve) => setTimeout(resolve, 5));
    }
    const cleanup = groups.map((pid) => ({ group: pid, absent: !groupExists(pid) }));
    t.diagnostic(JSON.stringify({ cleanup }));
    assert.ok(
      cleanup.every(({ absent }) => absent),
      'owned test groups remain',
    );
  }
});

test('unclosed pipes produce terminal cleanup failure without pinning the supervisor', async () => {
  const { path } =
    await fixture(`import { spawn } from 'node:child_process'; import { writeFileSync } from 'node:fs';
    export function evaluate() {
      const escaped = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'],
        { detached: true, stdio: ['ignore', 1, 2] });
      escaped.unref(); writeFileSync(process.env.CUSTOM_PID_FILE, String(escaped.pid));
      return { passed: true, output: {} };
    }`);
  const pidFile = join(tmpdir(), `lotuspond-escaped-${process.pid}-${Date.now()}`);
  const resultFile = join(tmpdir(), `lotuspond-cleanup-result-${process.pid}-${Date.now()}`);
  cleanupPaths.push(pidFile, resultFile);
  const moduleUrl = new URL('./runner-execution.mjs', import.meta.url).href;
  const program = `const { writeFile } = await import('node:fs/promises');
    const { executeScenario } = await import(${JSON.stringify(moduleUrl)});
    const result = await executeScenario({ adapterPath: ${JSON.stringify(path)}, input: {}, expected: {},
      revision: 1, runId: 'run', scenarioId: 'scenario', timeoutMs: 1000,
      environment: { PATH: process.env.PATH, TMPDIR: process.env.TMPDIR,
        LOTUSPOND_RUNNER_ADAPTER_ENV: 'CUSTOM_PID_FILE', CUSTOM_PID_FILE: ${JSON.stringify(pidFile)} } });
    await writeFile(${JSON.stringify(resultFile)}, result.classification);`;
  const child = spawn(process.execPath, ['--input-type=module', '-e', program], {
    stdio: 'ignore',
  });
  try {
    assert.equal(await waitForChild(child), 0);
    assert.equal(await readFile(resultFile, 'utf8'), 'cleanup_failed');
  } finally {
    const escapedPid = Number(await readFile(pidFile, 'utf8'));
    try {
      process.kill(escapedPid, 'SIGKILL');
    } catch {}
  }
});

test('an already-aborted scenario never continues adapter work', async () => {
  const { path } = await fixture('export function evaluate() { while (true) {} }');
  const controller = new AbortController();
  controller.abort();
  const result = await executeScenario(base(path, { signal: controller.signal }));
  assert.equal(result.classification, 'canceled');
  assert.equal(result.processGroupGone, true);
});

test('oversized scenario input is rejected before an adapter process starts', async () => {
  const { path } = await fixture(
    'export function evaluate() { return { passed: true, output: {} }; }',
  );
  const result = await executeScenario(base(path, { input: { prompt: 'x'.repeat(1024 * 1024) } }));
  assert.equal(result.classification, 'input_limit');
  assert.equal(result.latencyMs, 0);
  assert.equal(result.processGroupGone, true);
});

test('oversized stdout and crashes return classifications without raw output', async () => {
  const noisy = await fixture(
    `export function evaluate() { process.stdout.write('secret'.repeat(200000)); return { passed: true, output: {} }; }`,
  );
  const oversized = await executeScenario(base(noisy.path));
  assert.equal(oversized.classification, 'output_limit');
  assert.equal(JSON.stringify(oversized).includes('secret'), false);

  const crashed = await fixture('export function evaluate() { process.exit(19); }');
  const crash = await executeScenario(base(crashed.path));
  assert.equal(crash.classification, 'crash');
  assert.equal(crash.processGroupGone, true);
});

test('adapter exceptions become a fixed classification without retaining the message', async () => {
  const { path } = await fixture(
    "export function evaluate() { throw new Error('private-adapter-detail'); }",
  );
  const result = await executeScenario(base(path));
  assert.equal(result.classification, 'adapter_failure');
  assert.equal(JSON.stringify(result).includes('private-adapter-detail'), false);
});

test('successful leader cleanup terminates its lingering descendant', async () => {
  const { path } = await fixture(`import { spawn } from 'node:child_process';
    import { writeFileSync } from 'node:fs';
    export function evaluate() {
      const child = spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { stdio: 'ignore' });
      child.unref();
      writeFileSync(process.env.CUSTOM_PID_FILE, String(child.pid));
      return { passed: true, output: {} };
    }`);
  const pidFile = join(tmpdir(), `lotuspond-descendant-${process.pid}-${Date.now()}`);
  cleanupPaths.push(pidFile);
  const result = await executeScenario(
    base(path, {
      environment: {
        PATH: process.env.PATH,
        TMPDIR: process.env.TMPDIR,
        LOTUSPOND_RUNNER_ADAPTER_ENV: 'CUSTOM_PID_FILE',
        CUSTOM_PID_FILE: pidFile,
      },
    }),
  );
  assert.equal(result.classification, 'success');
  assert.equal(result.processGroupGone, true);
  const pid = Number(await readFile(pidFile, 'utf8'));
  assert.throws(() => process.kill(pid, 0), { code: 'ESRCH' });
});

test('adapter environment excludes control-plane credentials and permits explicit custom values', () => {
  const child = adapterEnvironment({
    PATH: '/bin',
    LOTUSPOND_API_KEY: 'control',
    LOTUSPOND_DATABASE_URL: 'postgres',
    LOTUSPOND_MFA_ENCRYPTION_KEY: 'mfa',
    LOTUSPOND_MCP_TOKEN: 'adapter-token',
    LOTUSPOND_RUNNER_ADAPTER_ENV: 'CUSTOM_PROVIDER_TOKEN',
    CUSTOM_PROVIDER_TOKEN: 'custom',
  });
  assert.deepEqual(child, {
    PATH: '/bin',
    LOTUSPOND_MCP_TOKEN: 'adapter-token',
    CUSTOM_PROVIDER_TOKEN: 'custom',
  });
  assert.throws(
    () => adapterEnvironment({ LOTUSPOND_RUNNER_ADAPTER_ENV: 'LOTUSPOND_API_KEY' }),
    /Reserved/,
  );
  assert.throws(
    () => adapterEnvironment({ LOTUSPOND_RUNNER_ADAPTER_ENV: 'NODE_OPTIONS' }),
    /Reserved/,
  );
});

test('runner timing configuration is bounded', () => {
  assert.equal(scenarioTimeout({}), 60000);
  assert.equal(scenarioTimeout({ LOTUSPOND_RUNNER_SCENARIO_TIMEOUT_MS: '1000' }), 1000);
  assert.equal(renewalInterval({ LOTUSPOND_RUNNER_RENEW_INTERVAL_MS: '1500' }), 1500);
  assert.throws(() => scenarioTimeout({ LOTUSPOND_RUNNER_SCENARIO_TIMEOUT_MS: '999' }), /between/);
  assert.throws(() => renewalInterval({ LOTUSPOND_RUNNER_RENEW_INTERVAL_MS: '60001' }), /between/);
});

test('renewal failure aborts active execution and remains authoritative', async () => {
  const controller = new AbortController();
  let renewals = 0;
  const renewal = startLeaseRenewal({
    renew: async () => {
      renewals += 1;
      throw new Error('lease lost');
    },
    intervalMs: 10,
    abortController: controller,
  });
  await new Promise((resolve) =>
    controller.signal.addEventListener('abort', resolve, { once: true }),
  );
  await renewal.stop();
  assert.equal(renewals, 1);
  assert.equal(renewal.lost, true);
});

test('aggregate results over the client request limit become a complete unknown batch', () => {
  const large = Array.from({ length: 3 }, (_, index) => ({
    scenario_id: `scenario-${index}`,
    passed: true,
    output: { value: 'x'.repeat(800_000) },
    evidence: { cost_measurement: 'unavailable' },
    latency_ms: index + 1,
  }));
  const bounded = boundedSubmissionResults(large, 'synthetic-lease');
  assert.equal(bounded.length, large.length);
  assert.deepEqual(
    bounded.map((result) => result.passed),
    [null, null, null],
  );
  assert.deepEqual(
    bounded.map((result) => result.latency_ms),
    [1, 2, 3],
  );
  assert.ok(
    Buffer.byteLength(JSON.stringify({ lease_token: 'synthetic-lease', results: bounded })) <
      2 * 1024 * 1024,
  );
});

test('per-scenario budgeting reserves room for every required result', () => {
  const candidate = {
    scenario_id: 'scenario-1',
    passed: true,
    output: { value: 'x'.repeat(1024 * 1024) },
    evidence: { cost_measurement: 'unavailable' },
    latency_ms: 1,
  };
  const first = boundedScenarioResult(candidate, 'synthetic-lease', 2);
  const second = boundedScenarioResult(
    { ...candidate, scenario_id: 'scenario-2' },
    'synthetic-lease',
    2,
  );
  const body = JSON.stringify({ lease_token: 'synthetic-lease', results: [first, second] });
  assert.ok(Buffer.byteLength(body) <= 2 * 1024 * 1024);
  assert.deepEqual([first.passed, second.passed], [null, null]);
});

test('an adapter root confines which code the runner may import', async () => {
  const root = await mkdtemp(join(tmpdir(), 'lotuspond-adapter-'));
  const inside = join(root, 'adapter.mjs');
  await writeFile(inside, 'export default async () => ({});');
  const outsideDir = await mkdtemp(join(tmpdir(), 'lotuspond-outside-'));
  const outside = join(outsideDir, 'adapter.mjs');
  await writeFile(outside, 'export default async () => ({});');
  try {
    // A process group is a lifecycle bound, not a privilege bound. Without a configured
    // root the operator's own path is used unchanged, which is the documented default.
    assert.equal(confinedAdapterPath(inside), resolve(inside));

    const environment = { LOTUSPOND_ADAPTER_ROOT: root };
    assert.equal(confinedAdapterPath(inside, environment), realpathSync(inside));
    assert.throws(
      () => confinedAdapterPath(outside, environment),
      /outside LOTUSPOND_ADAPTER_ROOT/,
    );
    assert.throws(
      () => confinedAdapterPath(join(root, '..', 'escape.mjs'), environment),
      /must both exist|outside LOTUSPOND_ADAPTER_ROOT/,
    );

    // A symlink inside the root that points out of it is resolved before the check.
    const link = join(root, 'link.mjs');
    await symlink(outside, link);
    assert.throws(() => confinedAdapterPath(link, environment), /outside LOTUSPOND_ADAPTER_ROOT/);
  } finally {
    await rm(root, { recursive: true, force: true });
    await rm(outsideDir, { recursive: true, force: true });
  }
});
