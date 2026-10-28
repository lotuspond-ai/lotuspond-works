import type { ReactNode } from 'react';
import { exactTime, relativeTime } from '../lib/format.js';
import { InkGlyph, type GlyphName } from './InkGlyph.js';

export function SectionHeader({
  title,
  description,
  eyebrow,
  actions,
}: {
  title: string;
  description?: string;
  eyebrow?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="page-heading">
      <div>
        {eyebrow && <p className="eyebrow">{eyebrow}</p>}
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {actions && <div className="heading-actions">{actions}</div>}
    </div>
  );
}
// An empty list shows the field-record glyph for what it will hold, then its one next action.
export function EmptyState({
  title,
  description,
  action,
  glyph,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
  glyph?: GlyphName;
}) {
  return (
    <div className="empty">
      {glyph && <InkGlyph name={glyph} />}
      <h2>{title}</h2>
      {description && <p>{description}</p>}
      {action}
    </div>
  );
}
// A table cell's time: relative in words, exact in the title, machine-readable in dateTime.
export function RelativeTime({ value }: { value: unknown }) {
  if (typeof value !== 'string' || !value) return <>Not recorded</>;
  return (
    <time className="cell-time" dateTime={value} title={exactTime(value)}>
      {relativeTime(value)}
    </time>
  );
}
export function Alert({ children, onRetry }: { children: ReactNode; onRetry?: () => void }) {
  return (
    <div className="error" role="alert">
      {children}
      {onRetry && (
        <button className="text-button" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  );
}
export function StatusBadge({ value }: { value: unknown }) {
  const status = String(value ?? 'unknown');
  const tone = [
    'pass',
    'passed',
    'approved',
    'ok',
    'active',
    'completed',
    'resolved',
    'verified',
    'accepted',
    'enabled',
  ].includes(status)
    ? 'success'
    : ['block', 'blocked', 'failed', 'error', 'rejected', 'suspended', 'critical'].includes(status)
      ? 'danger'
      : [
            'pending',
            'queued',
            'running',
            'open',
            'high',
            'require_approval',
            'approval_required',
            'approval',
          ].includes(status)
        ? 'warning'
        : 'neutral';
  return (
    <span className={`badge badge-${tone}`}>
      <span aria-hidden="true" />
      {humanize(status)}
    </span>
  );
}
// Whether a product capability can be used now, as docs/CAPABILITIES.md records it.
export function Readiness({ state }: { state: 'available' | 'planned' }) {
  return (
    <span className={`badge badge-${state === 'available' ? 'success' : 'neutral'}`}>
      <span aria-hidden="true" />
      {state === 'available' ? 'Available' : 'Planned'}
    </span>
  );
}
export function humanize(value: string): string {
  return value
    .replaceAll('_', ' ')
    .replaceAll('-', ' ')
    .replace(/^./, (letter) => letter.toUpperCase());
}
export function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return 'Not recorded';
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (typeof value === 'number')
    return value.toLocaleString(undefined, { maximumFractionDigits: 4 });
  if (
    typeof value === 'string' &&
    /^\d{4}-\d{2}-\d{2}T/.test(value) &&
    !Number.isNaN(Date.parse(value))
  )
    return new Date(value).toLocaleString();
  return String(value);
}
export function ObjectView({ value, depth = 0 }: { value: unknown; depth?: number }) {
  if (depth >= 8 && value && typeof value === 'object')
    return (
      <span className="hint">Further nested evidence is available in the technical record.</span>
    );
  if (value === null || typeof value !== 'object')
    return <span className="record-value">{displayValue(value)}</span>;
  if (Array.isArray(value))
    return value.length ? (
      <ol className="record-list">
        {value.slice(0, 100).map((item, index) => (
          <li key={index}>
            <ObjectView value={item} depth={depth + 1} />
          </li>
        ))}
        {value.length > 100 && (
          <li className="hint">
            {value.length - 100} additional items are available in the technical record.
          </li>
        )}
      </ol>
    ) : (
      <span className="hint">None recorded</span>
    );
  const entries = Object.entries(value as Record<string, unknown>);
  if (!entries.length) return <span className="hint">No additional information recorded</span>;
  return (
    <dl className={`record-facts ${depth > 0 ? 'record-facts-nested' : ''}`}>
      {entries.slice(0, 100).map(([key, item]) => (
        <div key={key} className={item && typeof item === 'object' ? 'wide' : ''}>
          <dt>{humanize(key)}</dt>
          <dd>
            {['status', 'verdict', 'severity'].includes(key) ? (
              <StatusBadge value={item} />
            ) : (
              <ObjectView value={item} depth={depth + 1} />
            )}
          </dd>
        </div>
      ))}
      {entries.length > 100 && (
        <div className="wide">
          <dt>Additional fields</dt>
          <dd>{entries.length - 100} further fields are available in the technical record.</dd>
        </div>
      )}
    </dl>
  );
}
export function RawRecord({ value }: { value: unknown }) {
  return (
    <details className="raw-record">
      <summary>View technical record</summary>
      <pre>{JSON.stringify(value, null, 2)}</pre>
    </details>
  );
}
