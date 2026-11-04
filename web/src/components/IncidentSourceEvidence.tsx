import { useEffect, useState } from 'react';
import { api } from '../api.js';
import { TraceDetails, type TraceDetail } from './TraceDetails.js';
import { Alert } from './ui.js';

export function IncidentSourceEvidence({ workspace, id }: { workspace: string; id: string }) {
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<TraceDetail | null>(null);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setDetail(null);
    setError('');
    void api<TraceDetail>(
      `/traces/${encodeURIComponent(id)}`,
      workspace,
      undefined,
      'GET',
      controller.signal,
    )
      .then((value) => {
        if (!controller.signal.aborted) setDetail(value);
      })
      .catch((reason: Error) => {
        if (!controller.signal.aborted) setError(reason.message);
      });
    return () => controller.abort();
  }, [open, workspace, id, revision]);
  const panelId = `incident-source-trace-${id}`;
  return (
    <section className="evidence-section">
      <div className="section-row">
        <h3>Trace evidence</h3>
        <button
          className="text-button"
          aria-expanded={open}
          aria-controls={panelId}
          onClick={() => setOpen(!open)}
        >
          {open ? 'Hide evidence' : 'Inspect trace'}
        </button>
      </div>
      <div id={panelId} hidden={!open}>
        {open &&
          (error ? (
            <Alert onRetry={() => setRevision((value) => value + 1)}>{error}</Alert>
          ) : !detail ? (
            <p role="status">Loading source evidence…</p>
          ) : (
            <TraceDetails
              value={detail}
              workspace={workspace}
              role="viewer"
              onRecorded={async () => {}}
            />
          ))}
      </div>
    </section>
  );
}
