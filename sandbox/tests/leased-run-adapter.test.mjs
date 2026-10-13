import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import test from 'node:test';
import {
  mapControllerResult,
  requireReviewedScenario,
  runController,
} from '../leased-run-adapter.mjs';

function result(classification = 'pass') {
  return {
    schema_version: 'lotuspond.gym-agent-result.v1',
    classification,
    receipt_sha256: 'a'.repeat(64),
    receipt: {
      schema_version: 'lotuspond.gym-agent-receipt.v1',
      environment_id: 'example.basic_x11@0.1',
      task_id: 'hello@1',
      status: classification === 'pass' ? 'passed' : 'task_failed',
      actions: [{ action: { type: 'finish' }, step: 2 }],
      agent_transport: 'loopback_http',
      runtime_image: 'sha256:2c37be8d20e6a6b205dbe071624b5154d1307034dc771e857fa10b7208b4eb7c',
      upstream_revision: 'f3dc04649641dba69d5032f7bb6adaa9785c150d',
      task_sha256: '3bf803ced81563661a8b7acb4163d8028e975c674ea7b4399d813e4595d07156',
      verifier_sha256: 'cc464c142074d4e22a23eeb05a35fa07bf57130211b90fea11f5edc087b2a4a6',
      agent_actions_sha256: 'd'.repeat(64),
      observation_screen_sha256: ['e'.repeat(64)],
      cleanup_confirmed: true,
      runtime_security_checks: {
        cap_drop_all: true,
        image: true,
        memory: true,
        nano_cpus: true,
        network_none: true,
        no_binds: true,
        no_devices: true,
        no_new_privileges: true,
        nonroot: true,
        not_privileged: true,
        pids: true,
        private_ipc: true,
        read_only: true,
        tmpfs_noexec: true,
        tmpfs_targets: true,
      },
      independent_application_state: {
        agent_finished: true,
        screen_captured: true,
        transcript_exact: true,
        window_present: true,
      },
      upstream_verifier: { passed: true, score: 100 },
    },
  };
}

test('accepts only the fixed reviewed scenario selector', () => {
  requireReviewedScenario({
    gym_environment_id: 'example.basic_x11@0.1',
    gym_task_id: 'hello@1',
  });
  assert.throws(() =>
    requireReviewedScenario({
      gym_environment_id: 'example.basic_x11@0.1',
      gym_task_id: 'hello@1',
      runtime_image: 'operator-input-is-not-allowed',
    }),
  );
});

test('maps authoritative pass and retains provenance', () => {
  const mapped = mapControllerResult(result());
  assert.equal(mapped.passed, true);
  assert.equal(mapped.evidence.cleanup_confirmed, true);
  assert.equal(mapped.evidence.cost_measurement, 'unavailable');
});

test('maps a completed verifier miss to task false', () => {
  const fixture = result('task_failure');
  fixture.receipt.independent_application_state.transcript_exact = false;
  fixture.receipt.upstream_verifier = { passed: false, score: 0 };
  assert.equal(mapControllerResult(fixture).passed, false);
});

test('throws for infrastructure, cleanup, security, or weak pass evidence', () => {
  for (const mutate of [
    (value) => (value.classification = 'infrastructure_failure'),
    (value) => (value.receipt.cleanup_confirmed = false),
    (value) => (value.receipt.runtime_security_checks.network_none = false),
    (value) => (value.receipt.runtime_security_checks = {}),
    (value) => (value.receipt.independent_application_state = {}),
    (value) => (value.receipt.independent_application_state.transcript_exact = false),
  ]) {
    const fixture = result();
    mutate(fixture);
    assert.throws(() => mapControllerResult(fixture));
  }
});

test('controller watchdog interrupts then force-kills a stuck child', async () => {
  const previous = {
    agent: process.env.LOTUSPOND_GYM_AGENT_URL,
    runs: process.env.LOTUSPOND_GYM_RUNS_DIR,
  };
  process.env.LOTUSPOND_GYM_AGENT_URL = 'http://127.0.0.1:8765/v1/action';
  process.env.LOTUSPOND_GYM_RUNS_DIR = '/tmp/lotuspond-test-runs';
  const signals = [];
  const child = new EventEmitter();
  child.stdout = new PassThrough();
  child.stderr = new PassThrough();
  child.kill = (signal) => {
    signals.push(signal);
    if (signal === 'SIGKILL') queueMicrotask(() => child.emit('close', null, 'SIGKILL'));
    return true;
  };
  try {
    await assert.rejects(
      runController(undefined, {
        spawnController: () => child,
        wallMilliseconds: 5,
        graceMilliseconds: 5,
      }),
      /wall-time limit/,
    );
  } finally {
    if (previous.agent === undefined) delete process.env.LOTUSPOND_GYM_AGENT_URL;
    else process.env.LOTUSPOND_GYM_AGENT_URL = previous.agent;
    if (previous.runs === undefined) delete process.env.LOTUSPOND_GYM_RUNS_DIR;
    else process.env.LOTUSPOND_GYM_RUNS_DIR = previous.runs;
  }
  assert.deepEqual(signals, ['SIGINT', 'SIGKILL']);
});
