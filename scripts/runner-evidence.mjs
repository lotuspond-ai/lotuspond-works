export function adapterResult(result, scenarioId, latencyMs) {
  if (
    (typeof result?.passed !== 'boolean' && result?.passed !== null) ||
    !result.output ||
    typeof result.output !== 'object' ||
    Array.isArray(result.output) ||
    (result.evidence !== undefined &&
      (!result.evidence || typeof result.evidence !== 'object' || Array.isArray(result.evidence)))
  )
    throw new Error('Adapter must return a verdict, object output, and optional object evidence.');
  const suppliedCost = result.costUsd !== undefined;
  if (
    suppliedCost &&
    (typeof result.costUsd !== 'number' || !Number.isFinite(result.costUsd) || result.costUsd < 0)
  )
    throw new Error('Adapter costUsd must be a finite nonnegative number when supplied.');
  return {
    scenario_id: scenarioId,
    passed: result.passed,
    output: result.output,
    ...(suppliedCost ? { cost_usd: result.costUsd } : {}),
    evidence: {
      ...result.evidence,
      cost_measurement:
        suppliedCost && result.evidence?.cost_measurement !== 'unavailable'
          ? 'reported'
          : 'unavailable',
    },
    latency_ms: latencyMs,
  };
}

export function executionFailure(scenarioId, latencyMs) {
  return {
    scenario_id: scenarioId,
    passed: null,
    output: { error: 'Evaluation failed. Inspect the runner locally.' },
    evidence: { execution: 'failed', cost_measurement: 'unavailable' },
    latency_ms: latencyMs,
  };
}
