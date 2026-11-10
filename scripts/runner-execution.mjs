import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

export const MAX_IPC_BYTES = 1024 * 1024;
export const DEFAULT_TIMEOUT_MS = 60000;
const CLEANUP_GRACE_MS = 300;
const COOPERATIVE_GRACE_MS = 5000;
const FIXED_ADAPTER_ENV = new Set([
  'LOTUSPOND_HTTP_ENDPOINT',
  'LOTUSPOND_HTTP_MODE',
  'LOTUSPOND_HTTP_TOKEN',
  'LOTUSPOND_HTTP_RESPONSE_PATH',
  'LOTUSPOND_HTTP_MODEL',
  'LOTUSPOND_HTTP_SYSTEM_PROMPT',
  'LOTUSPOND_HTTP_TIMEOUT_MS',
  'LOTUSPOND_HTTP_MAX_INPUT_BYTES',
  'LOTUSPOND_HTTP_MAX_RESPONSE_BYTES',
  'LOTUSPOND_HTTP_MAX_OUTPUT_BYTES',
]);
const BASE_ENV = new Set(['PATH', 'TMPDIR', 'LANG', 'LC_ALL']);
const RESERVED = new Set(['NODE_OPTIONS', 'NODE_PATH', 'LD_PRELOAD']);
const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

function waitForPromiseOrDeadline(promise, milliseconds) {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (completed) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(completed);
    };
    const timer = setTimeout(() => finish(false), milliseconds);
    timer.unref();
    promise.then(() => finish(true));
  });
}

function releaseChildHandles(child) {
  child.stdin?.destroy();
  child.stdout?.destroy();
  child.stderr?.destroy();
  child.unref();
}

function boundedInteger(environment, name, fallback, minimum, maximum) {
  const raw = environment[name];
  if (raw === undefined || raw === '') return fallback;
  if (!/^\d+$/.test(raw)) throw new Error(`${name} must be an integer.`);
  const value = Number(raw);
  if (value < minimum || value > maximum)
    throw new Error(`${name} must be between ${minimum} and ${maximum}.`);
  return value;
}

export const scenarioTimeout = (environment = process.env) =>
  boundedInteger(
    environment,
    'LOTUSPOND_RUNNER_SCENARIO_TIMEOUT_MS',
    DEFAULT_TIMEOUT_MS,
    1000,
    60000,
  );
export const renewalInterval = (environment = process.env) =>
  boundedInteger(environment, 'LOTUSPOND_RUNNER_RENEW_INTERVAL_MS', 60000, 1000, 60000);

export function adapterEnvironment(environment = process.env) {
  const requested = (environment.LOTUSPOND_RUNNER_ADAPTER_ENV || '')
    .split(',')
    .map((value) => value.trim())
    .filter(Boolean);
  if (requested.length > 32) throw new Error('Too many custom adapter environment variables.');
  const custom = new Set();
  for (const name of requested) {
    if (!/^[A-Z_][A-Z0-9_]{0,63}$/.test(name))
      throw new Error('Invalid custom adapter environment variable name.');
    if (name.startsWith('LOTUSPOND_') || name.startsWith('DYLD_') || RESERVED.has(name))
      throw new Error('Reserved environment variables cannot be forwarded to adapters.');
    custom.add(name);
  }
  const childEnvironment = {};
  for (const name of [...BASE_ENV, ...FIXED_ADAPTER_ENV, ...custom]) {
    if (environment[name] !== undefined) childEnvironment[name] = environment[name];
  }
  return childEnvironment;
}

function groupExists(pid) {
  try {
    process.kill(-pid, 0);
    return true;
  } catch (error) {
    return error?.code !== 'ESRCH';
  }
}

async function terminateGroup(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  if (!groupExists(pid)) return true;
  try {
    process.kill(-pid, 'SIGTERM');
  } catch (error) {
    if (error?.code !== 'ESRCH') return false;
  }
  const deadline = Date.now() + CLEANUP_GRACE_MS;
  while (Date.now() < deadline && groupExists(pid)) await sleep(20);
  if (groupExists(pid)) {
    try {
      process.kill(-pid, 'SIGKILL');
    } catch (error) {
      if (error?.code !== 'ESRCH') return false;
    }
  }
  const killDeadline = Date.now() + CLEANUP_GRACE_MS;
  while (Date.now() < killDeadline && groupExists(pid)) await sleep(20);
  return !groupExists(pid);
}

export async function executeScenario({
  adapterPath,
  input,
  expected,
  revision,
  runId,
  scenarioId,
  timeoutMs = DEFAULT_TIMEOUT_MS,
  cleanupGraceMs = COOPERATIVE_GRACE_MS,
  signal,
  environment = process.env,
}) {
  if (process.platform === 'win32')
    throw new Error('Runner subprocess cleanup requires POSIX process-group semantics.');
  if (!Number.isInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > DEFAULT_TIMEOUT_MS)
    throw new Error('Invalid scenario timeout.');
  if (
    !Number.isInteger(cleanupGraceMs) ||
    cleanupGraceMs < 1 ||
    cleanupGraceMs > COOPERATIVE_GRACE_MS
  )
    throw new Error('Invalid cleanup grace.');
  const payload = Buffer.from(
    JSON.stringify({ input, expected, revision, runId, scenarioId }),
    'utf8',
  );
  if (payload.length > MAX_IPC_BYTES)
    return { classification: 'input_limit', latencyMs: 0, processGroupGone: true };

  const started = performance.now();
  const childPath = fileURLToPath(new URL('./runner-execution-child.mjs', import.meta.url));
  const child = spawn(process.execPath, [childPath, adapterPath], {
    detached: true,
    env: adapterEnvironment(environment),
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  let classification;
  let stdoutBytes = 0;
  let stderrBytes = 0;
  const stdout = [];
  let exitCode = null;
  let cleanupPromise;
  let resolveExit;
  let resolveClose;
  let resolveCleanup;
  const exitedPromise = new Promise((resolve) => {
    resolveExit = resolve;
  });
  const closedPromise = new Promise((resolve) => {
    resolveClose = resolve;
  });
  const cleanupFinished = new Promise((resolve) => {
    resolveCleanup = resolve;
  });
  const trackCleanup = (operation) => {
    operation.then(resolveCleanup, () => resolveCleanup(false));
    return operation;
  };
  const beginForcedCleanup = () => {
    cleanupPromise ||= trackCleanup(terminateGroup(child.pid));
    return cleanupPromise;
  };
  const beginCooperativeCleanup = () => {
    cleanupPromise ||= trackCleanup(
      (async () => {
        try {
          child.kill('SIGINT');
        } catch {}
        await waitForPromiseOrDeadline(exitedPromise, cleanupGraceMs);
        return terminateGroup(child.pid);
      })(),
    );
    return cleanupPromise;
  };
  const requestStop = (reason) => {
    classification ||= reason;
    void beginCooperativeCleanup();
  };
  const timer = setTimeout(() => requestStop('timeout'), timeoutMs);
  const abort = () => requestStop('canceled');
  signal?.addEventListener('abort', abort, { once: true });
  child.stdout.on('data', (chunk) => {
    stdoutBytes += chunk.length;
    if (stdoutBytes > MAX_IPC_BYTES) requestStop('output_limit');
    else stdout.push(chunk);
  });
  child.stderr.on('data', (chunk) => {
    stderrBytes += chunk.length;
    if (stderrBytes > 65536) requestStop('output_limit');
  });
  child.on('error', () => {
    classification ||= 'spawn_failed';
    resolveExit();
  });
  child.on('exit', (code) => {
    exitCode = code;
    resolveExit();
  });
  child.on('close', resolveClose);
  child.stdin.on('error', () => {});
  child.stdin.end(payload);
  if (signal?.aborted) abort();
  const first = await Promise.race([
    exitedPromise.then(() => ({ kind: 'exit' })),
    cleanupFinished.then((groupGone) => ({ kind: 'cleanup', groupGone })),
  ]);
  clearTimeout(timer);
  signal?.removeEventListener('abort', abort);
  const latencyMs = Math.max(0, Math.round(performance.now() - started));
  if (first.kind === 'cleanup' && !first.groupGone) {
    releaseChildHandles(child);
    return { classification: 'cleanup_failed', latencyMs, processGroupGone: false };
  }
  if (classification === 'spawn_failed' && !Number.isInteger(child.pid))
    return { classification, latencyMs, processGroupGone: true };
  const cleanupConfirmed = await (cleanupPromise || beginForcedCleanup());
  if (!cleanupConfirmed) {
    releaseChildHandles(child);
    return { classification: 'cleanup_failed', latencyMs, processGroupGone: false };
  }
  const pipesClosed = await waitForPromiseOrDeadline(closedPromise, 300);
  if (!pipesClosed) {
    releaseChildHandles(child);
    return { classification: 'cleanup_failed', latencyMs, processGroupGone: true };
  }
  if (classification) return { classification, latencyMs, processGroupGone: true };
  try {
    const envelope = JSON.parse(Buffer.concat(stdout).toString('utf8'));
    if (envelope?.status === 'failure')
      return { classification: 'adapter_failure', latencyMs, processGroupGone: true };
    if (exitCode === 0 && envelope?.status === 'success')
      return {
        classification: 'success',
        result: envelope.result,
        latencyMs,
        processGroupGone: true,
      };
    return { classification: 'crash', latencyMs, processGroupGone: true };
  } catch {
    return {
      classification: exitCode === 0 ? 'protocol_error' : 'crash',
      latencyMs,
      processGroupGone: true,
    };
  }
}

export function startLeaseRenewal({ renew, intervalMs = 60000, abortController }) {
  let stopped = false;
  let lost = false;
  let loss;
  let timer;
  let active = Promise.resolve();
  const tick = () => {
    if (stopped || lost) return;
    active = Promise.resolve()
      .then(renew)
      .catch((error) => {
        lost = true;
        loss = error;
        abortController.abort();
      })
      .finally(() => {
        if (!stopped && !lost) timer = setTimeout(tick, intervalMs);
      });
  };
  timer = setTimeout(tick, intervalMs);
  return {
    get lost() {
      return lost;
    },
    get loss() {
      return loss;
    },
    async stop() {
      stopped = true;
      clearTimeout(timer);
      await active;
    },
  };
}
