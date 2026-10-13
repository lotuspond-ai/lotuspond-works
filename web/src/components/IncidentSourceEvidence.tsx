import { useEffect, useState } from 'react';
import { api, type Row } from '../api.js';
import { TraceDetails, type TraceDetail } from './TraceDetails.js';
import { Assessments } from './Assessments.js';
import { Alert, StatusBadge, displayValue, humanize } from './ui.js';

type VoiceSource = { session: Row; segments: Row[]; assessments: Row[] };

export function IncidentSourceEvidence({
  workspace,
  kind,
  id,
}: {
  workspace: string;
  kind: 'trace' | 'voice';
  id: string;
}) {
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<TraceDetail | VoiceSource | null>(null);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setDetail(null);
    setError('');
    const path =
      kind === 'trace'
        ? `/traces/${encodeURIComponent(id)}`
        : `/voice/sessions/${encodeURIComponent(id)}`;
    void api<TraceDetail | VoiceSource>(path, workspace, undefined, 'GET', controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setDetail(value);
      })
      .catch((reason: Error) => {
        if (!controller.signal.aborted) setError(reason.message);
      });
    return () => controller.abort();
  }, [open, workspace, kind, id, revision]);
  const panelId = `incident-source-${kind}-${id}`;
  return (
    <section className="evidence-section">
      <div className="section-row">
        <h3>{kind === 'trace' ? 'Trace evidence' : 'Voice session evidence'}</h3>
        <button
          className="text-button"
          aria-expanded={open}
          aria-controls={panelId}
          onClick={() => setOpen(!open)}
        >
          {open ? 'Hide evidence' : `Inspect ${kind === 'trace' ? 'trace' : 'voice session'}`}
        </button>
      </div>
      <div id={panelId} hidden={!open}>
        {open && (
          <>
            {error ? (
              <Alert onRetry={() => setRevision((value) => value + 1)}>{error}</Alert>
            ) : !detail ? (
              <p role="status">Loading source evidence…</p>
            ) : 'trace' in detail ? (
              <TraceDetails
                value={detail}
                workspace={workspace}
                role="viewer"
                onRecorded={async () => {}}
              />
            ) : (
              <>
                <div className="section-row">
                  <h4>{String(detail.session.name || 'Voice session')}</h4>
                  <StatusBadge value={detail.session.status} />
                </div>
                <dl className="compact-facts">
                  <div>
                    <dt>Provider</dt>
                    <dd>{displayValue(detail.session.provider)}</dd>
                  </div>
                  <div>
                    <dt>Created</dt>
                    <dd>{displayValue(detail.session.created_at)}</dd>
                  </div>
                </dl>
                <h4>Retained transcript</h4>
                {detail.segments.length ? (
                  <ol className="incident-transcript">
                    {detail.segments.map((segment) => (
                      <li key={segment.id}>
                        <div>
                          <strong>{humanize(String(segment.speaker || 'other'))}</strong>
                          <span className="hint">
                            {segment.is_final === true ? 'Final segment' : 'Provisional segment'}
                          </span>
                        </div>
                        <p>{String(segment.text || 'No text recorded.')}</p>
                      </li>
                    ))}
                  </ol>
                ) : (
                  <p className="hint">No transcript segments are retained.</p>
                )}
                <Assessments
                  items={detail.assessments}
                  workspace={workspace}
                  resourcePath={`/voice/sessions/${encodeURIComponent(id)}`}
                  canWrite={false}
                  subject="transcript"
                  onRecorded={() => {}}
                />
              </>
            )}
          </>
        )}
      </div>
    </section>
  );
}
