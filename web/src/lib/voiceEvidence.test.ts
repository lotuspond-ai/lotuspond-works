import assert from 'node:assert/strict';
import test from 'node:test';
import { normalizeVoiceEvidence, voiceMetricRows, describePrivacyValue } from './voiceEvidence.js';

test('voice transcripts do not invent metric coverage or provider attestation', () => {
  const record = normalizeVoiceEvidence({
    id: 'session-1',
    status: 'closed',
    transcript: [{ role: 'user', text: 'Hello' }],
  });
  assert.equal(record?.turnCount, 1);
  assert.deepEqual(record?.metrics, {});
  assert.equal(record?.gateEvaluation, undefined);
  assert.equal(record?.provenance.providerAttested, undefined);
  assert.equal(record?.provenance.liveTelephony, undefined);
  assert.equal(record?.replay.eligible, false);
  assert.equal(record?.schemaVersion, 'lotuspond.voice-call.v1');
});
test('required voice gate with missing evidence never becomes a passing check', () => {
  const record = normalizeVoiceEvidence({
    gate_evaluation: { verdict: 'block', checks: [{ name: 'latency', coverage: 'missing' }] },
  });
  assert.equal(record?.gateEvaluation?.checks[0]?.passed, false);
  assert.equal(record?.gateEvaluation?.checks[0]?.covered, false);
});
test('opaque call references are retained but raw provider call identifiers are not exposed', () => {
  assert.equal(normalizeVoiceEvidence({ call_id: '+15551234567' })?.externalCallRef, undefined);
  assert.equal(
    normalizeVoiceEvidence({ call_id: 'call_0123456789abcdef' })?.externalCallRef,
    'call_0123456789abcdef',
  );
});
test('voice metrics retain units and explicit negative outcomes', () => {
  const rows = voiceMetricRows({
    task_success: false,
    latency: { p95_ms: 1200 },
    tool_correctness: 0.75,
  });
  assert.deepEqual(
    rows.map((row) => [row.label, row.value]),
    [
      ['Task success', 'No'],
      ['P95 latency', '1,200 ms'],
      ['Tool correctness', '75%'],
    ],
  );
  assert.equal(rows[0]?.failed, true);
});
test('invalid timing does not create fabricated duration', () => {
  assert.equal(
    normalizeVoiceEvidence({ started_at: 'invalid', ended_at: 'also-invalid' })?.durationMs,
    undefined,
  );
  assert.equal(
    normalizeVoiceEvidence({ started_at: '2026-01-01T00:01:00Z', ended_at: '2026-01-01T00:00:00Z' })
      ?.durationMs,
    undefined,
  );
});
test('missing privacy provenance stays explicitly unknown', () => {
  assert.equal(describePrivacyValue(undefined), 'Not supplied');
  assert.equal(
    describePrivacyValue({ raw_audio_retained: false }),
    'redaction metadata · raw audio not retained',
  );
});
