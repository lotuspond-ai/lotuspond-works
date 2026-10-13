export interface VoiceTurn {
  turnId?: string;
  speaker: string;
  text: string;
  startedAtMs?: number;
  endedAtMs?: number;
  intent?: string;
  workflowStep?: string;
}

export interface VoiceEvent {
  eventId?: string;
  stage: string;
  kind: string;
  turnId?: string;
  startedAtMs?: number;
  endedAtMs?: number;
  durationMs?: number;
  status?: string;
  details?: Record<string, unknown>;
}

export interface VoiceFailureAttribution {
  stage: string;
  cause: string;
  confidence?: number;
  evidence?: string;
}

export interface VoiceGateCheck {
  name: string;
  active: boolean;
  covered?: boolean;
  passed: boolean;
  actual?: unknown;
  expected?: unknown;
}

export interface VoiceGateEvaluation {
  verdict: string;
  passed?: boolean;
  score?: number;
  checks: VoiceGateCheck[];
}

export interface NormalizedVoiceEvidence {
  id: string;
  schemaVersion: string;
  providerName: string;
  /** Opaque, retention-safe reference. Raw provider call IDs are never rendered. */
  externalCallRef?: string;
  projectId?: string;
  status: string;
  outcome?: string;
  mode?: string;
  startedAt?: string;
  endedAt?: string;
  durationMs?: number;
  turns: VoiceTurn[];
  turnCount: number;
  events: VoiceEvent[];
  eventCount: number;
  metrics: Record<string, unknown>;
  gateEvaluation?: VoiceGateEvaluation;
  metricCoverage?: Record<string, unknown>;
  failureAttribution: VoiceFailureAttribution[];
  provenance: {
    source?: string;
    providerAttested?: boolean;
    liveTelephony?: boolean;
  };
  privacy?: {
    consent?: unknown;
    redaction?: unknown;
    retention?: unknown;
  };
  replay: {
    eligible: boolean;
    reason?: string;
  };
  raw: Record<string, unknown>;
}

export interface VoiceMetricRow {
  key: string;
  label: string;
  value: string;
  failed?: boolean;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function asRecordArray(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value.map(asRecord).filter((item): item is Record<string, unknown> => Boolean(item))
    : [];
}

function firstRecord(...values: unknown[]): Record<string, unknown> {
  for (const value of values) {
    const record = asRecord(value);
    if (record) return record;
  }
  return {};
}

function firstString(...values: unknown[]): string | undefined {
  for (const value of values) {
    if (typeof value === 'string' && value.trim()) return value.trim();
    if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  }
  return undefined;
}

function firstNumber(...values: unknown[]): number | undefined {
  for (const value of values) {
    if (typeof value === 'number' && Number.isFinite(value)) return value;
    if (typeof value === 'string' && value.trim() && Number.isFinite(Number(value)))
      return Number(value);
  }
  return undefined;
}

function firstBoolean(...values: unknown[]): boolean | undefined {
  for (const value of values) {
    if (typeof value === 'boolean') return value;
    if (value === 'true') return true;
    if (value === 'false') return false;
  }
  return undefined;
}

function normalizeTurns(value: unknown): VoiceTurn[] {
  return asRecordArray(value).map((turn, index) => ({
    turnId: firstString(turn.turnId, turn.turn_id, turn.id) || `turn-${index + 1}`,
    speaker: firstString(turn.speaker, turn.role) || 'unknown',
    text: firstString(turn.text, turn.transcript, turn.content) || '',
    startedAtMs: firstNumber(turn.startedAtMs, turn.started_at_ms, turn.startMs),
    endedAtMs: firstNumber(turn.endedAtMs, turn.ended_at_ms, turn.endMs),
    intent: firstString(turn.intent),
    workflowStep: firstString(turn.workflowStep, turn.workflow_step),
  }));
}

function normalizeEvents(value: unknown): VoiceEvent[] {
  return asRecordArray(value).map((event, index) => ({
    eventId: firstString(event.eventId, event.event_id, event.id) || `event-${index + 1}`,
    stage: firstString(event.stage, event.layer, event.source) || 'unknown',
    kind: firstString(event.kind, event.type, event.name) || 'event',
    turnId: firstString(event.turnId, event.turn_id),
    startedAtMs: firstNumber(event.startedAtMs, event.started_at_ms),
    endedAtMs: firstNumber(event.endedAtMs, event.ended_at_ms),
    durationMs: firstNumber(event.durationMs, event.duration_ms, event.latencyMs, event.latency_ms),
    status: firstString(event.status, event.result),
    details: asRecord(event.details) || undefined,
  }));
}

function attributionFromRecord(record: Record<string, unknown>): VoiceFailureAttribution {
  return {
    stage:
      firstString(
        record.stage,
        record.layer,
        record.primaryStage,
        record.primary_stage,
        record.category,
        record.source,
      ) || 'unattributed',
    cause:
      firstString(
        record.cause,
        record.likelyCause,
        record.likely_cause,
        record.reason,
        record.message,
        record.detail,
      ) || 'No cause supplied',
    confidence: firstNumber(record.confidence, record.score),
    evidence: firstString(
      record.evidence,
      record.signal,
      record.eventKind,
      record.event_kind,
      record.gate,
    ),
  };
}

function normalizeAttribution(value: unknown): VoiceFailureAttribution[] {
  if (Array.isArray(value)) return asRecordArray(value).map(attributionFromRecord);
  const record = asRecord(value);
  if (!record || Object.keys(record).length === 0) return [];

  const nested = asRecordArray(record.layers || record.attributions || record.failures);
  if (nested.length > 0) return nested.map(attributionFromRecord);

  const hasCanonicalFields = [
    'stage',
    'layer',
    'primaryStage',
    'primary_stage',
    'cause',
    'likelyCause',
    'reason',
  ].some((key) => record[key] !== undefined);
  if (hasCanonicalFields) return [attributionFromRecord(record)];

  return Object.entries(record).flatMap(([stage, detail]) => {
    const nestedDetail = asRecord(detail);
    if (nestedDetail) return [{ ...attributionFromRecord(nestedDetail), stage }];
    const cause = firstString(detail);
    return cause ? [{ stage, cause }] : [];
  });
}

function normalizeGateEvaluation(value: unknown): VoiceGateEvaluation | undefined {
  const record = asRecord(value);
  if (!record || Object.keys(record).length === 0) return undefined;
  const checks = asRecordArray(record.checks).map((check, index) => {
    const coverage = firstString(check.coverage)?.toLowerCase();
    return {
      name: firstString(check.name, check.gate) || `gate-${index + 1}`,
      active: firstBoolean(check.active) ?? true,
      covered:
        firstBoolean(check.covered) ??
        (coverage === 'covered' ? true : coverage === 'missing' ? false : undefined),
      passed: firstBoolean(check.passed) ?? false,
      actual: check.actual,
      expected: check.expected,
    };
  });
  const passed = firstBoolean(record.passed);
  return {
    verdict:
      firstString(record.verdict) ||
      (passed === true ? 'pass' : passed === false ? 'block' : 'unknown'),
    passed,
    score: firstNumber(record.score),
    checks,
  };
}

function mergeRecords(...values: unknown[]): Record<string, unknown> {
  return Object.assign({}, ...values.map(asRecord).filter(Boolean));
}

export function normalizeVoiceEvidence(
  value: unknown,
  fallbackId = 'voice-evidence',
): NormalizedVoiceEvidence | null {
  const root = asRecord(value);
  if (!root) return null;

  const summary = firstRecord(root.summary);
  const voiceEvidence = firstRecord(
    root.voiceEvidence,
    root.voice_evidence,
    summary.voiceEvidence,
    summary.voice_evidence,
  );
  const source = Object.keys(voiceEvidence).length > 0 ? { ...root, ...voiceEvidence } : root;
  const provider = firstRecord(source.provider);
  const timing = firstRecord(source.timing);
  const outcomeRecord = firstRecord(source.outcome);
  const privacy = firstRecord(source.privacy);
  const replay = firstRecord(source.replay);
  const ingestion = firstRecord(source.ingestion, summary.ingestion);
  const provenanceRecord = firstRecord(source.provenance, summary.provenance);
  const gateEvaluation = normalizeGateEvaluation(
    source.gateEvaluation ||
      source.gate_evaluation ||
      summary.gateEvaluation ||
      summary.gate_evaluation,
  );
  const metricCoverage = firstRecord(
    source.metricCoverage,
    source.metric_coverage,
    summary.metricCoverage,
    summary.metric_coverage,
    firstRecord(source.gateEvaluation, source.gate_evaluation).metricCoverage,
  );
  const startedAt = firstString(
    timing.startedAt,
    timing.started_at,
    source.startedAt,
    source.started_at,
  );
  const endedAt = firstString(timing.endedAt, timing.ended_at, source.endedAt, source.ended_at);
  const suppliedDuration = firstNumber(
    timing.durationMs,
    timing.duration_ms,
    source.durationMs,
    source.duration_ms,
  );
  const computedDuration =
    startedAt && endedAt ? new Date(endedAt).getTime() - new Date(startedAt).getTime() : undefined;
  const taskSuccess = firstBoolean(outcomeRecord.taskSuccess, outcomeRecord.task_success);

  const turns = normalizeTurns(source.turns || source.transcript || source.conversation);
  const events = normalizeEvents(source.events || source.pipelineEvents || source.pipeline_events);
  const metrics = mergeRecords(
    source.metrics,
    source.voiceMetrics,
    source.voice_metrics,
    summary.voiceMetrics,
    summary.voice_metrics,
  );
  const failureAttribution = normalizeAttribution(
    source.failureAttribution ||
      source.failure_attribution ||
      summary.failureAttribution ||
      summary.failure_attribution,
  );
  const turnCount = firstNumber(source.turnCount, source.turn_count) ?? turns.length;
  const eventCount = firstNumber(source.eventCount, source.event_count) ?? events.length;
  const rootRedaction = source.redaction;
  const runtimeCallReference = firstString(source.callId, source.call_id);
  const safeRuntimeCallReference =
    runtimeCallReference && /^call_[a-f0-9]{16,64}$/i.test(runtimeCallReference)
      ? runtimeCallReference
      : undefined;
  const normalizedPrivacy =
    Object.keys(privacy).length > 0
      ? {
          consent: privacy.consent,
          redaction: privacy.redaction,
          retention: privacy.retention,
        }
      : rootRedaction !== undefined
        ? { redaction: rootRedaction }
        : undefined;

  return {
    id: firstString(source.id, source.voiceCallId, source.voice_call_id, fallbackId) || fallbackId,
    schemaVersion:
      firstString(source.schemaVersion, source.schema_version) || 'lotuspond.voice-call.v1',
    providerName:
      firstString(
        provider.name,
        source.providerName,
        source.provider_name,
        firstRecord(source.callerProfile, source.caller_profile).provider,
        typeof source.provider === 'string' ? source.provider : undefined,
      ) || 'custom',
    externalCallRef: firstString(
      provider.externalCallRef,
      provider.external_call_ref,
      provider.externalCallIdHash,
      provider.external_call_id_hash,
      source.externalCallRef,
      source.external_call_ref,
      source.externalCallIdHash,
      source.external_call_id_hash,
      safeRuntimeCallReference,
    ),
    projectId: firstString(source.projectId, source.project_id),
    status: firstString(source.status, outcomeRecord.status) || 'recorded',
    outcome: firstString(
      outcomeRecord.code,
      outcomeRecord.label,
      source.outcomeCode,
      source.outcome_code,
      typeof source.outcome === 'string' ? source.outcome : undefined,
      taskSuccess === true ? 'task_succeeded' : taskSuccess === false ? 'task_failed' : undefined,
    ),
    mode: firstString(source.mode, source.source, source.evidenceMode, source.evidence_mode),
    startedAt,
    endedAt,
    durationMs:
      suppliedDuration ??
      (computedDuration !== undefined && Number.isFinite(computedDuration) && computedDuration >= 0
        ? computedDuration
        : undefined),
    turns,
    turnCount,
    events,
    eventCount,
    metrics,
    gateEvaluation,
    metricCoverage: Object.keys(metricCoverage).length > 0 ? metricCoverage : undefined,
    failureAttribution,
    provenance: {
      source: firstString(ingestion.source, provenanceRecord.source, source.source, source.mode),
      providerAttested: firstBoolean(
        ingestion.providerAttested,
        ingestion.provider_attested,
        provenanceRecord.providerAttested,
        provenanceRecord.provider_attested,
      ),
      liveTelephony: firstBoolean(
        source.liveTelephony,
        source.live_telephony,
        provenanceRecord.liveTelephony,
        provenanceRecord.live_telephony,
      ),
    },
    privacy: normalizedPrivacy,
    replay: {
      eligible:
        firstBoolean(replay.eligible, source.replayEligible, source.replay_eligible) ?? false,
      reason: firstString(replay.reason, source.replayReason, source.replay_reason),
    },
    raw: source,
  };
}

export function normalizeVoiceEvidenceCollection(payload: unknown): NormalizedVoiceEvidence[] {
  const root = asRecord(payload);
  const data = root ? root.data : payload;
  const dataRecord = asRecord(data);
  const candidates = Array.isArray(data)
    ? data
    : dataRecord?.data ||
      dataRecord?.items ||
      dataRecord?.voiceCalls ||
      dataRecord?.voice_calls ||
      dataRecord?.calls ||
      root?.items ||
      root?.voiceCalls ||
      root?.voice_calls ||
      root?.calls ||
      [];

  return (Array.isArray(candidates) ? candidates : [])
    .map((item, index) => normalizeVoiceEvidence(item, `voice-evidence-${index + 1}`))
    .filter((item): item is NormalizedVoiceEvidence => Boolean(item));
}

export function voiceEvidenceFromRun(run: Record<string, unknown>): NormalizedVoiceEvidence[] {
  const summary = firstRecord(run.summary);
  const evidenceCandidates: unknown[] = [];
  const direct =
    run.voiceEvidence || run.voice_evidence || summary.voiceEvidence || summary.voice_evidence;
  const directMetrics =
    run.voiceMetrics || run.voice_metrics || summary.voiceMetrics || summary.voice_metrics;
  const directAttribution =
    run.failureAttribution ||
    run.failure_attribution ||
    summary.failureAttribution ||
    summary.failure_attribution;

  if (Array.isArray(direct)) {
    evidenceCandidates.push(
      ...direct.map((item) => ({
        summary,
        ...(asRecord(item) || {}),
        ...(directMetrics ? { voiceMetrics: directMetrics } : {}),
        ...(directAttribution ? { failureAttribution: directAttribution } : {}),
      })),
    );
  } else if (direct) {
    evidenceCandidates.push({
      summary,
      ...(asRecord(direct) || {}),
      ...(directMetrics ? { voiceMetrics: directMetrics } : {}),
      ...(directAttribution ? { failureAttribution: directAttribution } : {}),
    });
  }

  const attempts = asRecordArray(run.attempts);
  for (const attempt of attempts) {
    const attemptSummary = firstRecord(attempt.summary);
    const attemptDetails = firstRecord(attempt.details);
    const attemptEvidence =
      attempt.voiceEvidence ||
      attempt.voice_evidence ||
      attemptSummary.voiceEvidence ||
      attemptSummary.voice_evidence ||
      attemptDetails.voiceEvidence ||
      attemptDetails.voice_evidence;
    if (Array.isArray(attemptEvidence)) {
      evidenceCandidates.push(
        ...attemptEvidence.map((item) => ({
          ...attempt,
          ...(asRecord(item) || {}),
        })),
      );
      continue;
    }
    if (attemptEvidence) {
      evidenceCandidates.push({ ...attempt, ...(asRecord(attemptEvidence) || {}) });
      continue;
    }

    const metrics =
      attempt.voiceMetrics ||
      attempt.voice_metrics ||
      attemptSummary.voiceMetrics ||
      attemptSummary.voice_metrics ||
      attemptDetails.voiceMetrics ||
      attemptDetails.voice_metrics;
    const attribution =
      attempt.failureAttribution ||
      attempt.failure_attribution ||
      attemptSummary.failureAttribution ||
      attemptSummary.failure_attribution ||
      attemptDetails.failureAttribution ||
      attemptDetails.failure_attribution;
    if (metrics || attribution) {
      evidenceCandidates.push({
        id: attempt.id,
        status: attempt.status,
        startedAt: attempt.startedAt,
        endedAt: attempt.finishedAt,
        voiceMetrics: metrics,
        failureAttribution: attribution,
      });
    }
  }

  if (
    evidenceCandidates.length === 0 &&
    (summary.voiceMetrics ||
      summary.voice_metrics ||
      summary.failureAttribution ||
      summary.failure_attribution)
  ) {
    evidenceCandidates.push({
      id: run.id,
      status: run.status,
      startedAt: run.startedAt,
      endedAt: run.finishedAt,
      voiceMetrics: summary.voiceMetrics || summary.voice_metrics,
      failureAttribution: summary.failureAttribution || summary.failure_attribution,
    });
  }

  return evidenceCandidates
    .map((item, index) =>
      normalizeVoiceEvidence(item, `${String(run.id || 'run')}-voice-${index + 1}`),
    )
    .filter((item): item is NormalizedVoiceEvidence => Boolean(item));
}

function flattenMetrics(value: unknown, prefix = '', depth = 0): Array<[string, unknown]> {
  if (depth > 2) return [];
  const record = asRecord(value);
  if (!record) return [];

  return Object.entries(record).flatMap(([key, nested]) => {
    const path = prefix ? `${prefix}.${key}` : key;
    if (asRecord(nested)) return flattenMetrics(nested, path, depth + 1);
    if (Array.isArray(nested)) return [];
    return [[path, nested] as [string, unknown]];
  });
}

function metricLabel(path: string): string {
  const labels: Record<string, string> = {
    task_success: 'Task success',
    workflow_success: 'Workflow success',
    'latency.average_ms': 'Average latency',
    'latency.p95_ms': 'P95 latency',
    'latency.max_ms': 'Maximum latency',
    'interruption.false_rate': 'False interruption rate',
    'interruption.false_interruption_rate': 'False interruption rate',
    'interruption.missed_rate': 'Missed barge-in rate',
    'interruption.missed_barge_in_rate': 'Missed barge-in rate',
    'interruption.coherence_average': 'Post-interruption coherence',
    'interruption.post_interruption_coherence': 'Post-interruption coherence',
    'interruption.resolution_average_ms': 'Interruption resolution',
    'interruption.resolution_max_ms': 'Maximum interruption resolution',
    'tool.correctness': 'Tool correctness',
    tool_correctness: 'Tool correctness',
    hallucinations: 'Hallucinations',
    hallucination_count: 'Hallucinations',
    guardrail_violations: 'Guardrail violations',
    guardrail_violation_count: 'Guardrail violations',
  };
  if (labels[path]) return labels[path];
  return path
    .replace(/\./g, ' · ')
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

function metricValue(path: string, value: unknown): string {
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (typeof value === 'number') {
    if (path.endsWith('_ms') || path.endsWith('Ms'))
      return `${Math.round(value).toLocaleString('en-US')} ms`;
    if (path.endsWith('rate') || path.includes('correctness') || path.includes('coherence')) {
      return `${Math.round(value * 100)}%`;
    }
    return value.toLocaleString('en-US', { maximumFractionDigits: 3 });
  }
  if (value === null || value === undefined || value === '') return '—';
  return String(value);
}

function metricFailed(path: string, value: unknown): boolean | undefined {
  if (typeof value === 'boolean' && (path === 'task_success' || path === 'workflow_success'))
    return !value;
  if (typeof value !== 'number') return undefined;
  if (path.includes('hallucination') || path.includes('guardrail_violation')) return value > 0;
  return undefined;
}

export function voiceMetricRows(metrics: Record<string, unknown>, limit = 12): VoiceMetricRow[] {
  const preferredOrder = [
    'task_success',
    'workflow_success',
    'latency.p95_ms',
    'latency.average_ms',
    'interruption.false_rate',
    'interruption.false_interruption_rate',
    'interruption.missed_rate',
    'interruption.missed_barge_in_rate',
    'interruption.coherence_average',
    'interruption.post_interruption_coherence',
    'interruption.resolution_average_ms',
    'interruption.resolution_max_ms',
    'tool.correctness',
    'tool_correctness',
    'hallucinations',
    'hallucination_count',
    'guardrail_violations',
    'guardrail_violation_count',
  ];
  const order = new Map(preferredOrder.map((key, index) => [key, index]));

  return flattenMetrics(metrics)
    .filter(([, value]) => value !== undefined && value !== null)
    .sort(([a], [b]) => (order.get(a) ?? 999) - (order.get(b) ?? 999) || a.localeCompare(b))
    .slice(0, limit)
    .map(([key, value]) => ({
      key,
      label: metricLabel(key),
      value: metricValue(key, value),
      failed: metricFailed(key, value),
    }));
}

export function formatVoiceDuration(durationMs?: number): string {
  if (durationMs === undefined || !Number.isFinite(durationMs)) return 'Duration unavailable';
  if (durationMs < 1000) return `${Math.round(durationMs)} ms`;
  if (durationMs < 60_000) return `${(durationMs / 1000).toFixed(1)} sec`;
  const minutes = Math.floor(durationMs / 60_000);
  const seconds = Math.round((durationMs % 60_000) / 1000);
  return `${minutes}m ${seconds}s`;
}

export function formatVoiceTime(value?: string): string {
  if (!value) return 'Time unavailable';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'Time unavailable';
  return date.toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function describePrivacyValue(value: unknown): string {
  if (typeof value === 'boolean') return value ? 'Recorded' : 'Not recorded';
  if (typeof value === 'string' && value.trim()) return value.replace(/[_-]+/g, ' ');
  const record = asRecord(value);
  if (!record) return 'Not supplied';
  const retentionLike =
    record.expiresAt !== undefined ||
    record.retentionDays !== undefined ||
    record.scrubbed !== undefined ||
    record.scrubbedAt !== undefined;
  if (retentionLike) {
    const mode = firstString(record.mode)?.replace(/[_-]+/g, ' ');
    const scrubbed = firstBoolean(record.scrubbed);
    const scrubbedAt = firstString(record.scrubbedAt);
    const expiresAt = firstString(record.expiresAt);
    const retentionDays = firstNumber(record.retentionDays);
    const reason = firstString(record.reason)?.replace(/[_-]+/g, ' ');
    if (scrubbed) {
      return ['Scrubbed', scrubbedAt ? formatVoiceTime(scrubbedAt) : null, reason || null]
        .filter(Boolean)
        .join(' · ');
    }
    if (mode === 'indefinite') return 'Indefinite · no automatic scrub deadline';
    return [
      mode || 'retained',
      expiresAt ? `scrubs ${formatVoiceTime(expiresAt)}` : null,
      retentionDays !== undefined ? `${retentionDays} day${retentionDays === 1 ? '' : 's'}` : null,
      record.archiveEnabled === false ? 'no archive' : null,
    ]
      .filter(Boolean)
      .join(' · ');
  }
  const redactionLike =
    record.applied !== undefined ||
    record.providerConfirmed !== undefined ||
    record.piiPatternsRedacted !== undefined ||
    record.rawProviderEnvelopeStored !== undefined ||
    record.rawAudioRetained !== undefined ||
    record.raw_audio_retained !== undefined;
  if (redactionLike) {
    const mode = firstString(record.mode)?.replace(/[_-]+/g, ' ');
    const applied = firstBoolean(record.applied) ?? firstBoolean(record.piiPatternsRedacted);
    const providerConfirmed = firstBoolean(record.providerConfirmed, record.provider_confirmed);
    const rawAudioRetained = firstBoolean(record.rawAudioRetained, record.raw_audio_retained);
    const transcriptRetained = firstBoolean(
      record.transcriptTextRetained,
      record.transcript_text_retained,
    );
    return [
      mode ||
        (applied === true ? 'applied' : applied === false ? 'not applied' : 'redaction metadata'),
      providerConfirmed === true
        ? 'provider confirmed'
        : providerConfirmed === false
          ? 'provider not attested'
          : null,
      rawAudioRetained === false ? 'raw audio not retained' : null,
      transcriptRetained === false ? 'transcript text not retained' : null,
    ]
      .filter(Boolean)
      .join(' · ');
  }
  const status = firstString(record.status, record.mode, record.policy, record.result);
  if (status) return status.replace(/[_-]+/g, ' ');
  return 'Metadata supplied';
}
