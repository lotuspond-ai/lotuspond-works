import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { api, type Row } from '../api.js';
import { Alert, EmptyState, SectionHeader, StatusBadge, displayValue } from '../components/ui.js';
import { IncidentReview } from '../components/IncidentReview.js';
import {
  incidentCategory,
  incidentSource,
  type Incident,
  type IncidentDetail,
} from '../lib/incidents.js';

type Cursor = { before: string; before_id: string };
export function Incidents({
  workspace,
  projectId,
  role,
  onOpenProject,
}: {
  workspace: string;
  projectId: string;
  role: string;
  onOpenProject: (id: string) => void;
}) {
  const [rows, setRows] = useState<Incident[]>([]);
  const [next, setNext] = useState<Cursor | null>(null);
  const [status, setStatus] = useState('open');
  const [category, setCategory] = useState('');
  const [filter, setFilter] = useState('');
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const [selected, setSelected] = useState('');
  const [detail, setDetail] = useState<IncidentDetail | null>(null);
  const [detailError, setDetailError] = useState('');
  const [detailRevision, setDetailRevision] = useState(0);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [actionError, setActionError] = useState('');
  const [showForm, setShowForm] = useState(false);
  const [traces, setTraces] = useState<Row[]>([]);
  const [traceError, setTraceError] = useState('');
  const [traceLoading, setTraceLoading] = useState(false);
  const [createError, setCreateError] = useState('');
  const detailHeading = useRef<HTMLHeadingElement>(null);
  const statusFilter = useRef<HTMLSelectElement>(null);
  const reviewButton = useRef<HTMLButtonElement | null>(null);
  const pagination = useRef<AbortController | null>(null);
  const canWrite = role !== 'viewer';

  const load = useCallback(
    async (signal: AbortSignal, cursor?: Cursor) => {
      const query = new URLSearchParams({ limit: '100' });
      if (projectId) query.set('project_id', projectId);
      if (status) query.set('status', status);
      if (category) query.set('category', category);
      if (cursor) {
        query.set('before', cursor.before);
        query.set('before_id', cursor.before_id);
      }
      setError('');
      if (cursor) setLoadingMore(true);
      else {
        setLoading(true);
        setRows([]);
        setNext(null);
      }
      try {
        const result = await api<{ items: Incident[]; next: Cursor | null }>(
          `/incidents?${query}`,
          workspace,
          undefined,
          'GET',
          signal,
        );
        if (signal.aborted) return;
        setRows((current) =>
          cursor
            ? [
                ...current,
                ...result.items.filter((item) => !current.some((row) => row.id === item.id)),
              ]
            : result.items,
        );
        setNext(result.next);
      } catch (reason) {
        if (!signal.aborted) setError((reason as Error).message);
      } finally {
        if (!signal.aborted) {
          setLoading(false);
          setLoadingMore(false);
        }
      }
    },
    [workspace, projectId, status, category],
  );
  useEffect(() => {
    const controller = new AbortController();
    pagination.current?.abort();
    setLoadingMore(false);
    void load(controller.signal);
    return () => {
      controller.abort();
      pagination.current?.abort();
    };
  }, [load, revision]);
  useEffect(() => {
    setDetail(null);
    setDetailError('');
    setActionError('');
    if (!selected) return;
    const controller = new AbortController();
    void api<IncidentDetail>(
      `/incidents/${encodeURIComponent(selected)}`,
      workspace,
      undefined,
      'GET',
      controller.signal,
    )
      .then((value) => {
        if (!controller.signal.aborted) setDetail(value);
      })
      .catch((reason: Error) => {
        if (!controller.signal.aborted) setDetailError(reason.message);
      });
    return () => controller.abort();
  }, [workspace, selected, detailRevision]);
  useEffect(() => {
    if (detail) {
      detailHeading.current?.focus();
      detailHeading.current?.scrollIntoView({ block: 'start' });
    }
  }, [detail]);
  useEffect(() => {
    if (!showForm) return;
    const controller = new AbortController();
    setTraceLoading(true);
    setTraceError('');
    setTraces([]);
    const query = new URLSearchParams({ limit: '100' });
    if (projectId) query.set('project_id', projectId);
    void api<{ items: Row[] }>(`/traces?${query}`, workspace, undefined, 'GET', controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) setTraces(value.items);
      })
      .catch((reason: Error) => {
        if (!controller.signal.aborted) setTraceError(reason.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setTraceLoading(false);
      });
    return () => controller.abort();
  }, [showForm, workspace, projectId]);

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setCreateError('');
    setMessage('');
    const data = new FormData(event.currentTarget);
    const traceId = String(data.get('trace_id') || '');
    try {
      const created = await api<Incident>('/incidents', workspace, {
        title: String(data.get('title')).trim(),
        severity: data.get('severity'),
        ...(traceId ? { trace_id: traceId } : {}),
      });
      setShowForm(false);
      setSelected(created.id);
      setRevision((value) => value + 1);
      setMessage('Manual incident recorded. Review its evidence and alert records below.');
    } catch (reason) {
      setCreateError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function changeStatus(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!detail) return;
    const reason = String(new FormData(event.currentTarget).get('reason') || '').trim();
    if (reason.length < 3) {
      setActionError('Enter a reason with at least 3 characters.');
      return;
    }
    const action = detail.incident.status === 'open' ? 'resolve' : 'reopen';
    setBusy(true);
    setActionError('');
    setMessage('');
    try {
      await api(`/incidents/${encodeURIComponent(detail.incident.id)}/${action}`, workspace, {
        reason,
      });
      setMessage(
        action === 'resolve'
          ? 'Incident resolved. Its original assessment is unchanged.'
          : 'Incident reopened for further review.',
      );
      setRevision((value) => value + 1);
      setDetailRevision((value) => value + 1);
    } catch (reason) {
      setActionError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const visible = rows.filter((row) =>
    `${row.title} ${row.id} ${incidentSource(row.source)}`
      .toLowerCase()
      .includes(filter.toLowerCase()),
  );
  return (
    <>
      <SectionHeader
        eyebrow="Production evidence"
        title="Incidents"
        description="Review reported problems, the evidence that raised them, and the reasons behind each resolution."
        actions={
          <>
            <button
              className="secondary"
              disabled={loading || busy}
              onClick={() => {
                setRevision((value) => value + 1);
                setDetailRevision((value) => value + 1);
              }}
            >
              Refresh
            </button>
            {canWrite && (
              <button
                disabled={busy}
                onClick={() => {
                  setShowForm(!showForm);
                  setCreateError('');
                }}
              >
                {showForm ? 'Close form' : 'Report incident'}
              </button>
            )}
          </>
        }
      />
      {message && (
        <p className="success-notice" role="status">
          {message}
        </p>
      )}
      {showForm && canWrite && (
        <form
          className="record-form"
          onSubmit={(event) => {
            void create(event);
          }}
        >
          <h2>Report an incident</h2>
          <p>This records a manual report. It does not assign an automated assessment verdict.</p>
          <div className="form-grid">
            <label>
              Incident title
              <input name="title" required maxLength={160} autoFocus />
            </label>
            <label>
              Severity
              <select name="severity" defaultValue="medium">
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
                <option value="critical">Critical</option>
              </select>
            </label>
            <label className="wide">
              Related trace {projectId ? '' : <span className="optional">Optional</span>}
              <select
                name="trace_id"
                defaultValue=""
                required={!!projectId}
                disabled={traceLoading}
              >
                <option value="">
                  {traceLoading
                    ? 'Loading traces…'
                    : projectId
                      ? 'Choose a trace from this project…'
                      : 'No trace — workspace-wide report'}
                </option>
                {traces.map((trace) => (
                  <option key={trace.id} value={trace.id}>
                    {String(trace.name || trace.id)} · {displayValue(trace.created_at)}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <p className="hint">
            The project is taken from the linked trace.{' '}
            {projectId
              ? 'Choose All projects in the workspace sidebar to make a report without a trace.'
              : 'A report without a trace belongs to the workspace and has no project association.'}{' '}
            Only the 100 most recent matching traces are listed.
          </p>
          {traceError && <Alert>Traces could not be loaded. {traceError}</Alert>}
          {createError && <Alert>{createError}</Alert>}
          <button disabled={busy || (!!projectId && (traceLoading || !traces.length))}>
            {busy ? 'Recording…' : 'Record incident'}
          </button>
        </form>
      )}
      <div className="incident-filters">
        <label>
          Incident status
          <select
            ref={statusFilter}
            value={status}
            onChange={(event) => setStatus(event.target.value)}
          >
            <option value="open">Open</option>
            <option value="resolved">Resolved</option>
            <option value="">All statuses</option>
          </select>
        </label>
        <label>
          Incident category
          <select value={category} onChange={(event) => setCategory(event.target.value)}>
            <option value="">All categories</option>
            <option value="assessment_failed">Assessment failed</option>
            <option value="assessment_unavailable">Evaluation unavailable</option>
            <option value="manual">Manual report</option>
          </select>
        </label>
        <label>
          Search loaded incidents
          <input
            type="search"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            placeholder="Title, incident ID, or source"
          />
        </label>
      </div>
      {error && <Alert onRetry={() => setRevision((value) => value + 1)}>{error}</Alert>}
      {loading ? (
        <p className="loading-state" role="status">
          Loading incidents…
        </p>
      ) : !visible.length && !error ? (
        <EmptyState
          title="No incidents match this view"
          description="Change the status, category, project, or search filter to review other records."
        />
      ) : (
        <>
          <div className="table-wrap">
            <table>
              <caption className="sr-only">Incidents</caption>
              <thead>
                <tr>
                  <th scope="col">Incident</th>
                  <th scope="col">Category</th>
                  <th scope="col">Severity</th>
                  <th scope="col">Status</th>
                  <th scope="col">Created</th>
                  <th scope="col">Action</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((incident) => (
                  <tr
                    key={incident.id}
                    className={selected === incident.id ? 'incident-selected' : undefined}
                  >
                    <td className="primary-cell">
                      <strong>{incident.title}</strong>
                      <span className="incident-source-label">
                        {incidentSource(incident.source)}
                      </span>
                    </td>
                    <td>{incidentCategory(incident.category).label}</td>
                    <td>
                      <StatusBadge value={incident.severity} />
                    </td>
                    <td>
                      <StatusBadge value={incident.status} />
                    </td>
                    <td>{displayValue(incident.created_at)}</td>
                    <td className="actions">
                      <button
                        className="text-button"
                        disabled={busy}
                        aria-label={`Review ${incident.title}`}
                        onClick={(event) => {
                          reviewButton.current = event.currentTarget;
                          setSelected(incident.id);
                          setDetailRevision((value) => value + 1);
                          setMessage('');
                        }}
                      >
                        Review
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {/* Outside the list, so narrowing the view to nothing is announced as well. */}
      {!loading && rows.length > 0 && (
        <p className="hint" role="status">
          Showing {visible.length} of {rows.length} loaded incidents.
        </p>
      )}
      {next && !loading && (
        <button
          className="secondary"
          disabled={loadingMore}
          onClick={() => {
            pagination.current?.abort();
            pagination.current = new AbortController();
            void load(pagination.current.signal, next);
          }}
        >
          {loadingMore ? 'Loading…' : 'Load more incidents'}
        </button>
      )}
      {selected && (
        <section className="incident-detail" aria-labelledby="incident-review-heading">
          <div className="section-row">
            <h2 id="incident-review-heading" ref={detailHeading} tabIndex={-1}>
              {detail?.incident.title || 'Incident review'}
            </h2>
            <button
              className="text-button"
              disabled={busy}
              onClick={() => {
                setSelected('');
                setMessage('');
                if (reviewButton.current?.isConnected) reviewButton.current.focus();
                else statusFilter.current?.focus();
              }}
            >
              Close review
            </button>
          </div>
          {detailError ? (
            <Alert onRetry={() => setDetailRevision((value) => value + 1)}>{detailError}</Alert>
          ) : !detail ? (
            <p role="status">Loading incident evidence…</p>
          ) : (
            <>
              <IncidentReview detail={detail} workspace={workspace} onOpenProject={onOpenProject} />
              {canWrite ? (
                <form
                  key={`${detail.incident.id}-${detail.incident.status}`}
                  className="record-form"
                  onSubmit={(event) => {
                    void changeStatus(event);
                  }}
                >
                  <h3>
                    {detail.incident.status === 'open'
                      ? 'Resolve this incident'
                      : 'Reopen this incident'}
                  </h3>
                  <p>
                    {detail.incident.status === 'open'
                      ? 'Record what was investigated or changed. This updates the incident workflow; it does not alter the source evidence.'
                      : 'Explain why this incident needs further review. Its existing history is retained.'}
                  </p>
                  <label>
                    {detail.incident.status === 'open'
                      ? 'Resolution reason'
                      : 'Reason for reopening'}
                    <textarea name="reason" rows={3} required minLength={3} maxLength={2000} />
                  </label>
                  {actionError && <Alert>{actionError}</Alert>}
                  <button disabled={busy}>
                    {busy
                      ? 'Saving…'
                      : detail.incident.status === 'open'
                        ? 'Resolve incident'
                        : 'Reopen incident'}
                  </button>
                </form>
              ) : (
                <p className="hint">
                  You have read-only access. An editor or administrator can resolve or reopen this
                  incident.
                </p>
              )}
            </>
          )}
        </section>
      )}
    </>
  );
}
