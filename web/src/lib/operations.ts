export function elapsedSeconds(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value) || value < 0)
    return 'Unknown';
  if (value < 1) return 'Less than a second';
  const seconds = Math.floor(value);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${seconds % 60}s`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ${minutes % 60}m`;
  return `${Math.floor(hours / 24)}d ${hours % 24}h`;
}
export type WorkerRole = 'mail';
export type WorkerObservation = {
  state: 'fresh' | 'stale' | 'missing' | 'not_configured' | 'unavailable' | 'disabled';
  last_poll_at: string | null;
  age_seconds: number | null;
  fresh_for_seconds: number;
  error_code: string | null;
};
export type QueueState = {
  enabled: boolean;
  status_counts: Record<string, number>;
  eligible_now: number;
  suspended: number;
  delayed: number;
  expired_leases: number;
  oldest_eligible_age_seconds: number | null;
};
export type OperationsSnapshot = {
  observed_at: string;
  dependencies: {
    database: { state: 'ok' };
    redis: { state: 'ok' | 'not_configured' | 'unavailable' };
  };
  workers: Record<
    WorkerRole,
    { role: WorkerRole; enabled: boolean; observation: WorkerObservation }
  >;
  queues: Record<WorkerRole, QueueState>;
};

export function pollLabel(state: WorkerObservation['state']): string {
  return {
    fresh: 'Recent poll observed',
    stale: 'Poll observation is stale',
    missing: 'No poll recorded',
    not_configured: 'Observation store not configured',
    unavailable: 'Observation unavailable',
    disabled: 'Worker disabled',
  }[state];
}
