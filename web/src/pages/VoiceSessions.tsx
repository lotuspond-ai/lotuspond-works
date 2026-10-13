import { useEffect, useState, type FormEvent } from 'react';
import { api, type Row } from '../api.js';
import {
  Alert,
  EmptyState,
  RawRecord,
  SectionHeader,
  StatusBadge,
  displayValue,
} from '../components/ui.js';
import { Assessments } from '../components/Assessments.js';
import { normalizeVoiceEvidence, voiceMetricRows } from '../lib/voiceEvidence.js';

type VoiceDetail = { session: Row; segments: Row[]; assessments: Row[] };
export function VoiceSessions({
  workspace,
  projectId,
  role,
}: {
  workspace: string;
  projectId: string;
  role: string;
}) {
  const [rows, setRows] = useState<Row[]>([]);
  const [selected, setSelected] = useState('');
  const [detail, setDetail] = useState<VoiceDetail | null>(null);
  const [error, setError] = useState('');
  const [detailError, setDetailError] = useState('');
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [updated, setUpdated] = useState<Date | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [projects, setProjects] = useState<Row[]>([]);
  const [busy, setBusy] = useState(false);
  const canWrite = role !== 'viewer';
  useEffect(() => {
    const controller = new AbortController();
    void api<{ items: Row[] }>(
      '/projects?limit=100',
      workspace,
      undefined,
      'GET',
      controller.signal,
    )
      .then((result) => {
        if (!controller.signal.aborted) setProjects(result.items);
      })
      .catch((reason: Error) => {
        if (!controller.signal.aborted) setError(reason.message);
      });
    return () => controller.abort();
  }, [workspace]);
  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError('');
    const form = new FormData(event.currentTarget);
    try {
      const result = await api<Row>('/voice/sessions', workspace, {
        name: String(form.get('name')),
        project_id: String(form.get('project_id')),
        provider: String(form.get('provider')),
      });
      setShowForm(false);
      setSelected(result.id);
      setRefresh((value) => value + 1);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      try {
        const result = await api<{ items: Row[] }>(
          `/voice/sessions${projectId ? `?project_id=${encodeURIComponent(projectId)}` : ''}`,
          workspace,
          undefined,
          'GET',
          controller.signal,
        );
        if (!controller.signal.aborted) {
          setRows(result.items);
          setError('');
          setUpdated(new Date());
        }
      } catch (reason) {
        if (!controller.signal.aborted) setError((reason as Error).message);
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
          if (autoRefresh)
            timer = setTimeout(() => {
              if (document.visibilityState === 'visible') void load();
              else
                timer = setTimeout(() => {
                  void load();
                }, 5000);
            }, 5000);
        }
      }
    }
    void load();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [workspace, projectId, refresh, autoRefresh]);
  useEffect(() => {
    setDetail(null);
    setDetailError('');
    if (!selected) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      try {
        const result = await api<VoiceDetail>(
          `/voice/sessions/${encodeURIComponent(selected)}`,
          workspace,
          undefined,
          'GET',
          controller.signal,
        );
        if (!controller.signal.aborted) {
          setDetail(result);
          setDetailError('');
        }
      } catch (reason) {
        if (!controller.signal.aborted) setDetailError((reason as Error).message);
      } finally {
        if (!controller.signal.aborted && autoRefresh)
          timer = setTimeout(() => {
            void load();
          }, 5000);
      }
    }
    void load();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [selected, workspace, refresh, autoRefresh]);
  const visible = rows.filter((row) => !projectId || row.project_id === projectId);
  return (
    <>
      <SectionHeader
        eyebrow="Production evidence"
        title="Voice sessions"
        description="Follow submitted conversation transcripts and their assessments. Inspect the evidence behind every result, from an individual turn to the full session."
        actions={
          <>
            <button className="secondary" onClick={() => setRefresh((value) => value + 1)}>
              Refresh
            </button>
            {canWrite && (
              <button onClick={() => setShowForm(!showForm)}>
                {showForm ? 'Close form' : 'Create session'}
              </button>
            )}
          </>
        }
      />
      {showForm && (
        <form
          className="record-form"
          onSubmit={(event) => {
            void create(event);
          }}
        >
          <h2>Create a voice session</h2>
          <p>
            This creates an evidence record. Your voice application or transcription adapter must
            send transcript segments to this session.
          </p>
          <div className="form-grid">
            <label>
              Project
              <select name="project_id" defaultValue={projectId} required>
                <option value="">Choose a project…</option>
                {projects.map((project) => (
                  <option key={project.id} value={project.id}>
                    {String(project.name)}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Session name
              <input name="name" required maxLength={160} />
            </label>
            <label>
              Voice provider
              <input
                name="provider"
                required
                maxLength={80}
                placeholder="Your configured voice provider"
              />
            </label>
          </div>
          <button disabled={busy || !projects.length}>
            {busy ? 'Creating…' : 'Create session'}
          </button>
        </form>
      )}
      <div className="monitor-toolbar">
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={autoRefresh}
            onChange={(event) => setAutoRefresh(event.target.checked)}
          />
          Refresh every 5 seconds
        </label>
        <span className="hint">
          {updated ? `Last fetched ${updated.toLocaleTimeString()}` : 'Waiting for session records'}
        </span>
      </div>
      {error && <Alert onRetry={() => setRefresh((value) => value + 1)}>{error}</Alert>}
      {loading ? (
        <p className="loading-state" role="status">
          Loading voice sessions…
        </p>
      ) : !visible.length && !error ? (
        <EmptyState
          title="No voice sessions received"
          description="Connect your voice application or transcription pipeline to begin collecting session evidence. Incoming transcripts and model assessments will appear here."
          action={
            <a className="button-link secondary" href="#integrations">
              View integrations
            </a>
          }
        />
      ) : (
        <div className="master-detail">
          <div className="session-list" role="group" aria-label="Voice sessions">
            {visible.map((row) => (
              <button
                key={row.id}
                className={`session-row ${selected === row.id ? 'selected' : ''}`}
                aria-pressed={selected === row.id}
                onClick={() => setSelected(row.id)}
              >
                <span className="session-title">
                  {String(row.name || row.external_id || row.id.slice(0, 12))}
                </span>
                <StatusBadge value={row.status} />
                <span className="hint">
                  {String(row.provider || 'Provider not recorded')} · {displayValue(row.created_at)}
                </span>
              </button>
            ))}
          </div>
          <section className="session-detail" aria-label="Selected voice session">
            {!selected ? (
              <EmptyState
                title="Select a session"
                description="Review its transcript, measured voice metrics, and model assessments."
              />
            ) : detailError ? (
              <Alert onRetry={() => setRefresh((value) => value + 1)}>{detailError}</Alert>
            ) : !detail ? (
              <p role="status">Loading session evidence…</p>
            ) : (
              <VoiceRecord
                detail={detail}
                workspace={workspace}
                canWrite={canWrite}
                onAssessed={() => setRefresh((value) => value + 1)}
              />
            )}
          </section>
        </div>
      )}
      <p className="footnote">
        Transcripts alone do not establish audio quality, latency, interruption handling, or safety.
        Missing measurements remain unknown. Model assessments inform review; they do not authorize
        actions.
      </p>
    </>
  );
}
function VoiceRecord({
  detail,
  workspace,
  canWrite,
  onAssessed,
}: {
  detail: VoiceDetail;
  workspace: string;
  canWrite: boolean;
  onAssessed: () => void;
}) {
  const [closeError, setCloseError] = useState('');
  const [closing, setClosing] = useState(false);
  async function closeSession() {
    setClosing(true);
    setCloseError('');
    try {
      await api(`/voice/sessions/${encodeURIComponent(detail.session.id)}/close`, workspace, {});
      onAssessed();
    } catch (reason) {
      setCloseError((reason as Error).message);
    } finally {
      setClosing(false);
    }
  }
  const session = detail.session;
  const normalized = normalizeVoiceEvidence(session);
  const metrics = voiceMetricRows(normalized?.metrics || {});
  const segments = [...(detail.segments || [])].sort(
    (a, b) =>
      Number(a.sequence ?? a.sequence_number ?? 0) - Number(b.sequence ?? b.sequence_number ?? 0),
  );
  return (
    <>
      <div className="section-row">
        <div>
          <p className="eyebrow">Conversation evidence</p>
          <h2>{String(session.name || session.external_id || 'Voice session')}</h2>
        </div>
        <StatusBadge value={session.status} />
      </div>
      <dl className="compact-facts">
        <div>
          <dt>Provider</dt>
          <dd>{String(session.provider || 'Not recorded')}</dd>
        </div>
        <div>
          <dt>Started</dt>
          <dd>{displayValue(session.started_at || session.created_at)}</dd>
        </div>
        <div>
          <dt>Transcript segments</dt>
          <dd>{segments.length}</dd>
        </div>
      </dl>
      {metrics.length > 0 && (
        <section className="evidence-section">
          <h3>Measured voice metrics</h3>
          <dl className="compact-facts">
            {metrics.map((metric) => (
              <div key={metric.key}>
                <dt>{metric.label}</dt>
                <dd className={metric.failed ? 'danger' : ''}>{metric.value}</dd>
              </div>
            ))}
          </dl>
        </section>
      )}
      <section className="evidence-section">
        <h3>Transcript</h3>
        {!segments.length ? (
          <p className="hint">No transcript segments have been retained for this session.</p>
        ) : (
          <ol className="transcript">
            {segments.map((segment, index) => (
              <li key={segment.id || String(index)}>
                <div className="transcript-meta">
                  <strong>{String(segment.speaker || 'Unknown speaker')}</strong>
                  <span>
                    {segment.start_ms !== undefined || segment.started_at_ms !== undefined
                      ? `${(Number(segment.start_ms ?? segment.started_at_ms) / 1000).toFixed(1)}s`
                      : `Turn ${index + 1}`}
                  </span>
                  {segment.is_final === false && (
                    <span className="badge badge-neutral">Partial</span>
                  )}
                </div>
                <p>{String(segment.text || segment.transcript || 'No transcript text retained')}</p>
              </li>
            ))}
          </ol>
        )}
      </section>
      {session.status === 'open' && (
        <section className="evidence-section">
          <h3>Finalize the conversation</h3>
          <p className="hint">
            Close the session after all transcript segments are final. Assessments use a completed
            transcript; closed sessions cannot receive further segments.
          </p>
          {canWrite && (
            <button
              className="secondary"
              disabled={
                closing || !segments.length || segments.some((segment) => segment.is_final !== true)
              }
              onClick={() => {
                void closeSession();
              }}
            >
              {closing ? 'Closing…' : 'Close session for assessment'}
            </button>
          )}
          {closeError && <Alert>{closeError}</Alert>}
        </section>
      )}
      <Assessments
        items={detail.assessments || []}
        workspace={workspace}
        resourcePath={`/voice/sessions/${encodeURIComponent(detail.session.id)}`}
        canWrite={canWrite && session.status === 'closed'}
        subject="transcript"
        onRecorded={onAssessed}
      />
      <RawRecord value={detail} />
    </>
  );
}
