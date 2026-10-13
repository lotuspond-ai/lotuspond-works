import { spawn } from 'node:child_process';
import { dirname, isAbsolute, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const moduleDirectory = dirname(fileURLToPath(import.meta.url));
const bundle = resolve(moduleDirectory, 'real-app');
const source = resolve(moduleDirectory, 'src');
const maxControllerOutput = 1024 * 1024;
const maxControllerError = 16 * 1024;
const controllerWallMilliseconds = 55_000;
const cleanupGraceMilliseconds = 4_000;
const reviewed = {
  environmentId: 'example.basic_x11@0.1',
  taskId: 'hello@1',
  upstreamRevision: 'f3dc04649641dba69d5032f7bb6adaa9785c150d',
  taskSha256: '3bf803ced81563661a8b7acb4163d8028e975c674ea7b4399d813e4595d07156',
  verifierSha256: 'cc464c142074d4e22a23eeb05a35fa07bf57130211b90fea11f5edc087b2a4a6',
  runtimeImage: 'sha256:2c37be8d20e6a6b205dbe071624b5154d1307034dc771e857fa10b7208b4eb7c',
};
const runtimeChecks = [
  'cap_drop_all',
  'image',
  'memory',
  'nano_cpus',
  'network_none',
  'no_binds',
  'no_devices',
  'no_new_privileges',
  'nonroot',
  'not_privileged',
  'pids',
  'private_ipc',
  'read_only',
  'tmpfs_noexec',
  'tmpfs_targets',
];
const independentChecks = [
  'agent_finished',
  'screen_captured',
  'transcript_exact',
  'window_present',
];

function hasExactKeys(value, keys) {
  return (
    value &&
    typeof value === 'object' &&
    !Array.isArray(value) &&
    Object.keys(value).sort().join(',') === [...keys].sort().join(',')
  );
}

export function requireReviewedScenario(input) {
  if (
    !input ||
    typeof input !== 'object' ||
    Array.isArray(input) ||
    Object.keys(input).sort().join(',') !== 'gym_environment_id,gym_task_id' ||
    input.gym_environment_id !== reviewed.environmentId ||
    input.gym_task_id !== reviewed.taskId
  )
    throw new Error('Scenario does not select the reviewed Gym real-application task.');
}

function childEnvironment() {
  const allowed = ['PATH', 'HOME', 'TMPDIR', 'LANG', 'LC_ALL', 'DOCKER_HOST', 'DOCKER_CONTEXT'];
  const environment = Object.fromEntries(
    allowed
      .filter((name) => process.env[name] !== undefined)
      .map((name) => [name, process.env[name]]),
  );
  environment.PYTHONPATH = source;
  environment.PYTHONDONTWRITEBYTECODE = '1';
  return environment;
}

export function runController(signal, options = {}) {
  const outputDirectory = process.env.LOTUSPOND_GYM_RUNS_DIR;
  const agentUrl = process.env.LOTUSPOND_GYM_AGENT_URL;
  if (!outputDirectory || !isAbsolute(outputDirectory))
    throw new Error('LOTUSPOND_GYM_RUNS_DIR must be an absolute protected directory.');
  if (!agentUrl) throw new Error('LOTUSPOND_GYM_AGENT_URL is required.');
  const python = process.env.LOTUSPOND_GYM_PYTHON || 'python3.13';
  const args = [
    '-m',
    'lotuspond_sandbox.agent_loop',
    '--bundle',
    bundle,
    '--output-dir',
    outputDirectory,
    '--agent-url',
    agentUrl,
  ];
  const spawnController = options.spawnController || spawn;
  const wallMilliseconds = options.wallMilliseconds || controllerWallMilliseconds;
  const graceMilliseconds = options.graceMilliseconds || cleanupGraceMilliseconds;
  return new Promise((resolveResult, reject) => {
    const child = spawnController(python, args, {
      env: childEnvironment(),
      shell: false,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let stdout = Buffer.alloc(0);
    let stderr = Buffer.alloc(0);
    let outputExceeded = false;
    let timedOut = false;
    let forceTimer;
    const interrupt = () => {
      child.kill('SIGINT');
      forceTimer ??= setTimeout(() => child.kill('SIGKILL'), graceMilliseconds);
    };
    const watchdog = setTimeout(() => {
      timedOut = true;
      interrupt();
    }, wallMilliseconds);
    if (signal?.aborted) interrupt();
    signal?.addEventListener('abort', interrupt, { once: true });
    child.stdout.on('data', (chunk) => {
      stdout = Buffer.concat([stdout, chunk]);
      if (stdout.length > maxControllerOutput) {
        outputExceeded = true;
        interrupt();
      }
    });
    child.stderr.on('data', (chunk) => {
      if (stderr.length < maxControllerError)
        stderr = Buffer.concat([stderr, chunk]).subarray(0, maxControllerError);
    });
    child.once('error', (error) => {
      clearTimeout(watchdog);
      clearTimeout(forceTimer);
      signal?.removeEventListener('abort', interrupt);
      reject(error);
    });
    child.once('close', (code, terminationSignal) => {
      clearTimeout(watchdog);
      clearTimeout(forceTimer);
      signal?.removeEventListener('abort', interrupt);
      if (signal?.aborted) return reject(new Error('Gym controller was aborted.'));
      if (timedOut) return reject(new Error('Gym controller exceeded its wall-time limit.'));
      if (outputExceeded) return reject(new Error('Gym controller output exceeded its limit.'));
      if (code !== 0 || terminationSignal)
        return reject(new Error('Gym controller reported an infrastructure failure.'));
      try {
        resolveResult(JSON.parse(stdout.toString('utf8')));
      } catch {
        reject(new Error('Gym controller returned invalid JSON.'));
      }
    });
  });
}

export function mapControllerResult(result) {
  if (
    result?.schema_version !== 'lotuspond.gym-agent-result.v1' ||
    !['pass', 'task_failure', 'infrastructure_failure'].includes(result.classification) ||
    typeof result.receipt_sha256 !== 'string' ||
    !/^[0-9a-f]{64}$/.test(result.receipt_sha256) ||
    result.receipt?.schema_version !== 'lotuspond.gym-agent-receipt.v1'
  )
    throw new Error('Gym controller result does not match the reviewed schema.');
  const receipt = result.receipt;
  if (
    receipt.environment_id !== reviewed.environmentId ||
    receipt.task_id !== reviewed.taskId ||
    receipt.upstream_revision !== reviewed.upstreamRevision ||
    receipt.task_sha256 !== reviewed.taskSha256 ||
    receipt.verifier_sha256 !== reviewed.verifierSha256 ||
    receipt.runtime_image !== reviewed.runtimeImage ||
    typeof receipt.agent_actions_sha256 !== 'string' ||
    !/^[0-9a-f]{64}$/.test(receipt.agent_actions_sha256) ||
    !Array.isArray(receipt.observation_screen_sha256) ||
    receipt.observation_screen_sha256.length === 0 ||
    receipt.observation_screen_sha256.length > 7 ||
    receipt.observation_screen_sha256.some((value) => !/^[0-9a-f]{64}$/.test(value)) ||
    !Array.isArray(receipt.actions) ||
    receipt.actions.length > 6 ||
    result.classification === 'infrastructure_failure' ||
    receipt.cleanup_confirmed !== true ||
    !hasExactKeys(receipt.runtime_security_checks, runtimeChecks) ||
    runtimeChecks.some((key) => receipt.runtime_security_checks[key] !== true) ||
    !hasExactKeys(receipt.independent_application_state, independentChecks) ||
    !['passed', 'task_failed'].includes(receipt.status) ||
    (result.classification === 'pass') !== (receipt.status === 'passed')
  )
    throw new Error('Gym controller did not confirm infrastructure and cleanup.');
  const passed = result.classification === 'pass';
  if (
    passed &&
    (receipt.upstream_verifier?.passed !== true ||
      receipt.upstream_verifier?.score !== 100 ||
      independentChecks.some((key) => receipt.independent_application_state[key] !== true))
  )
    throw new Error('Gym pass lacks authoritative application evidence.');
  return {
    passed,
    output: {
      environment_id: receipt.environment_id,
      task_id: receipt.task_id,
      status: receipt.status,
      steps: receipt.actions.length,
    },
    evidence: {
      execution: 'completed',
      evaluator: 'gym-anything-real-application',
      adapter_transport: receipt.agent_transport,
      runtime_image: receipt.runtime_image,
      upstream_revision: receipt.upstream_revision,
      task_sha256: receipt.task_sha256,
      verifier_sha256: receipt.verifier_sha256,
      receipt_sha256: result.receipt_sha256,
      agent_actions_sha256: receipt.agent_actions_sha256,
      observation_screen_sha256: receipt.observation_screen_sha256,
      cleanup_confirmed: receipt.cleanup_confirmed,
      independent_application_state: receipt.independent_application_state,
      upstream_verifier: receipt.upstream_verifier,
      cost_measurement: 'unavailable',
    },
  };
}

export async function evaluate(input, expected, context) {
  void expected;
  requireReviewedScenario(input);
  const result = await runController(context?.signal);
  return mapControllerResult(result);
}
