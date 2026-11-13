import { realpathSync } from 'node:fs';
import { relative, resolve, sep } from 'node:path';
import { pathToFileURL } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { request } from './client.mjs';
import { adapterResult, executionFailure } from './runner-evidence.mjs';
import {
  executeScenario,
  renewalInterval,
  scenarioTimeout,
  startLeaseRenewal,
} from './runner-execution.mjs';

const MAX_SUBMISSION_BYTES = 2 * 1024 * 1024;

// A process group bounds an adapter's lifetime and resources; it is not a privilege
// boundary, and the adapter is the operator's own code running with the runner's own
// rights by design. LOTUSPOND_ADAPTER_ROOT lets an operator who runs the runner as a
// long-lived service state which directory may supply that code, so a dropped file or a
// rewritten argument elsewhere on the host cannot widen what gets imported. Symlinks are
// resolved first, so a link inside the root cannot point out of it.
export function confinedAdapterPath(adapter, environment = process.env) {
  const requested = resolve(adapter);
  const root = environment.LOTUSPOND_ADAPTER_ROOT;
  if (!root) return requested;
  let resolvedRoot;
  let resolvedAdapter;
  try {
    resolvedRoot = realpathSync(resolve(root));
    resolvedAdapter = realpathSync(requested);
  } catch {
    throw new Error('The adapter and LOTUSPOND_ADAPTER_ROOT must both exist.');
  }
  const within = relative(resolvedRoot, resolvedAdapter);
  if (within === '' || within.startsWith('..') || within.startsWith(sep) || within.includes('\0'))
    throw new Error('The adapter is outside LOTUSPOND_ADAPTER_ROOT.');
  return resolvedAdapter;
}

export function boundedSubmissionResults(results, leaseToken) {
  try {
    const payload = JSON.stringify({ lease_token: leaseToken, results });
    if (Buffer.byteLength(payload) <= MAX_SUBMISSION_BYTES) return results;
  } catch {}
  const fallback = results.map((result) =>
    executionFailureWithClassification(result.scenario_id, result.latency_ms, 'submission_limit'),
  );
  if (
    Buffer.byteLength(JSON.stringify({ lease_token: leaseToken, results: fallback })) >
    MAX_SUBMISSION_BYTES
  )
    throw new Error('Runner result batch exceeds the submission limit.');
  return fallback;
}

export function boundedScenarioResult(result, leaseToken, scenarioCount) {
  if (!Number.isInteger(scenarioCount) || scenarioCount < 1 || scenarioCount > 1000)
    throw new Error('Runner scenario count is invalid.');
  const envelopeBytes = Buffer.byteLength(JSON.stringify({ lease_token: leaseToken, results: [] }));
  const perScenarioBytes = Math.floor(
    (MAX_SUBMISSION_BYTES - envelopeBytes - (scenarioCount - 1)) / scenarioCount,
  );
  try {
    if (Buffer.byteLength(JSON.stringify(result)) <= perScenarioBytes) return result;
  } catch {}
  return executionFailureWithClassification(
    result.scenario_id,
    result.latency_ms,
    'submission_limit',
  );
}

function terminalAuthorizationFailure(error) {
  return error?.code === 'unauthorized' || error?.code === 'forbidden';
}

function executionFailureWithClassification(scenarioId, latencyMs, classification) {
  const failure = executionFailure(scenarioId, latencyMs);
  failure.evidence.execution_classification = classification;
  return failure;
}

export async function run({ environment = process.env, argv = process.argv } = {}) {
  const runner = environment.LOTUSPOND_RUNNER_ID;
  const adapter = argv[2];
  if (!runner || !/^[0-9a-f-]{36}$/i.test(runner) || !adapter)
    throw new Error(
      'Set LOTUSPOND_RUNNER_ID and provide a local evaluation adapter: npm run runner -- ./adapter.mjs',
    );
  const adapterPath = confinedAdapterPath(adapter, environment);
  const timeoutMs = scenarioTimeout(environment);
  const renewEveryMs = renewalInterval(environment);
  const once = environment.LOTUSPOND_RUNNER_ONCE === 'true';
  let stopping = false;
  let activeRun;
  const shutdown = new AbortController();
  const stop = () => {
    stopping = true;
    shutdown.abort();
    activeRun?.abort();
  };
  process.once('SIGTERM', stop);
  process.once('SIGINT', stop);
  console.log('LotusPond Works runner started. Waiting for queued evaluations.');
  try {
    while (!stopping) {
      let renewal;
      try {
        const claim = await request(`/runners/${runner}/claim`, {}, { signal: shutdown.signal });
        if (!claim.run) {
          if (once) break;
          await delay(3000, undefined, { signal: shutdown.signal });
          continue;
        }
        activeRun = new AbortController();
        renewal = startLeaseRenewal({
          renew: () =>
            request(
              `/runs/${claim.run.id}/renew`,
              { lease_token: claim.lease_token },
              { signal: activeRun.signal },
            ),
          intervalMs: renewEveryMs,
          abortController: activeRun,
        });
        const results = [];
        for (const scenario of claim.run.scenario_snapshot) {
          if (stopping || renewal.lost) break;
          const execution = await executeScenario({
            adapterPath,
            input: scenario.input,
            expected: scenario.expected,
            revision: claim.run.revision,
            runId: claim.run.id,
            scenarioId: scenario.id,
            timeoutMs,
            signal: activeRun.signal,
            environment,
          });
          if (execution.classification === 'cleanup_failed')
            throw Object.assign(
              new Error('Runner could not confirm adapter process-group cleanup.'),
              { code: 'cleanup_failed' },
            );
          if (stopping || renewal.lost || execution.classification === 'canceled') break;
          let result;
          if (execution.classification === 'success') {
            try {
              result = adapterResult(execution.result, scenario.id, execution.latencyMs);
            } catch {
              result = executionFailureWithClassification(
                scenario.id,
                execution.latencyMs,
                'invalid_result',
              );
            }
          } else
            result = executionFailureWithClassification(
              scenario.id,
              execution.latencyMs,
              execution.classification,
            );
          results.push(
            boundedScenarioResult(result, claim.lease_token, claim.run.scenario_snapshot.length),
          );
        }
        await renewal.stop();
        if (terminalAuthorizationFailure(renewal.loss)) throw renewal.loss;
        if (once && renewal.lost) throw new Error('Runner lease was lost.');
        if (!stopping && !renewal.lost && results.length === claim.run.scenario_snapshot.length) {
          const submissionResults = boundedSubmissionResults(results, claim.lease_token);
          await request(
            `/runs/${claim.run.id}/results`,
            { lease_token: claim.lease_token, results: submissionResults },
            { signal: activeRun.signal },
          );
          console.log(`Completed run ${claim.run.id}.`);
        }
        activeRun = undefined;
        if (once) break;
      } catch (error) {
        activeRun?.abort();
        await renewal?.stop();
        activeRun = undefined;
        if (stopping) break;
        if (error?.code === 'cleanup_failed') throw error;
        if (terminalAuthorizationFailure(error)) throw error;
        if (once) throw error;
        console.error(error instanceof Error ? error.message : 'Runner request failed.');
        await delay(3000, undefined, { signal: shutdown.signal }).catch(() => {});
      }
    }
  } finally {
    process.removeListener('SIGTERM', stop);
    process.removeListener('SIGINT', stop);
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  try {
    await run();
  } catch (error) {
    console.error(error instanceof Error ? error.message : 'Runner stopped.');
    process.exitCode = 1;
  }
}
