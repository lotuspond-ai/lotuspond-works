// Presentation helpers shared by the console and its public preview.

const labels: Record<string, string> = {
  name: 'Name',
  revision: 'Revision',
  status: 'Status',
  created_at: 'Created',
  latency_ms: 'Latency',
  cost_usd: 'Cost',
  occurred_at: 'Occurred',
  action: 'Action',
  actor_id: 'Actor',
  reason: 'Reason',
  email: 'Email',
  role: 'Role',
  prefix: 'Key prefix',
  scopes: 'Scopes',
  expires_at: 'Expires',
  revoked_at: 'Revoked',
  external_id: 'External ID',
  outcome: 'Outcome',
  capabilities: 'Capabilities',
  last_seen_at: 'Last seen',
  kind: 'Type',
};

export function fieldLabel(field: string) {
  return (
    labels[field] ?? field.replaceAll('_', ' ').replace(/^./, (letter) => letter.toUpperCase())
  );
}

const units: [Intl.RelativeTimeFormatUnit, number][] = [
  ['year', 31_536_000],
  ['month', 2_592_000],
  ['week', 604_800],
  ['day', 86_400],
  ['hour', 3_600],
  ['minute', 60],
];

// "3 hours ago" for recent times; the exact time belongs in the element's title.
export function relativeTime(value: string, now = Date.now()) {
  const seconds = (new Date(value).getTime() - now) / 1000;
  if (!Number.isFinite(seconds)) return value;
  const format = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' });
  for (const [unit, size] of units) {
    if (Math.abs(seconds) >= size) return format.format(Math.round(seconds / size), unit);
  }
  return seconds < 0 ? 'just now' : 'in a moment';
}

export function exactTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

export function formatLatency(milliseconds: number) {
  if (milliseconds < 1000) return `${Math.round(milliseconds)} ms`;
  const seconds = milliseconds / 1000;
  return `${Number.isInteger(seconds) ? seconds : seconds.toFixed(2)} s`;
}

export function initials(name: string) {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  return (
    ((parts[0]?.[0] ?? '') + (parts.length > 1 ? (parts.at(-1)?.[0] ?? '') : '')).toUpperCase() ||
    '·'
  );
}
