import { useEffect, useState, type FormEvent } from 'react';
import { api, type Row } from '../api.js';
import { Alert, EmptyState, RelativeTime, SectionHeader, StatusBadge } from '../components/ui.js';

export function Monitors({
  workspace,
  projectId,
  role,
}: {
  workspace: string;
  projectId: string;
  role: string;
}) {
  const [monitors, setMonitors] = useState<Row[]>([]);
  const [projects, setProjects] = useState<Row[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [showForm, setShowForm] = useState(false);
  const [busy, setBusy] = useState('');
  const [refresh, setRefresh] = useState(0);
  const canWrite = role !== 'viewer';

  useEffect(() => {
    const controller = new AbortController();
    const suffix = projectId ? `?project_id=${encodeURIComponent(projectId)}` : '';
    setLoading(true);
    Promise.all([
      api<{ items: Row[] }>(`/monitors${suffix}`, workspace, undefined, 'GET', controller.signal),
      api<{ items: Row[] }>('/projects?limit=100', workspace, undefined, 'GET', controller.signal),
    ])
      .then(([monitorResult, projectResult]) => {
        if (controller.signal.aborted) return;
        setMonitors(monitorResult.items);
        setProjects(projectResult.items);
        setError('');
      })
      .catch((reason: Error) => {
        if (!controller.signal.aborted) setError(reason.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [workspace, projectId, refresh]);

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const element = event.currentTarget;
    const form = new FormData(element);
    const percent = Number(form.get('sample_percent'));
    setBusy('create');
    setError('');
    try {
      if (!Number.isFinite(percent) || percent < 0 || percent > 100)
        throw new Error('Sample percentage must be between 0 and 100.');
      await api<Row>('/monitors', workspace, {
        project_id: projectId || String(form.get('project_id')),
        name: String(form.get('name')).trim(),
        criteria: String(form.get('criteria')).trim(),
        enabled: true,
        sample_basis_points: Math.round(percent * 100),
      });
      element.reset();
      setShowForm(false);
      setRefresh((value) => value + 1);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy('');
    }
  }

  async function setEnabled(monitor: Row, enabled: boolean) {
    setBusy(monitor.id);
    setError('');
    try {
      const updated = await api<Row>(
        `/monitors/${encodeURIComponent(monitor.id)}`,
        workspace,
        {
          project_id: String(monitor.project_id),
          name: String(monitor.name),
          criteria: String(monitor.criteria),
          enabled,
          sample_basis_points: Number(monitor.sample_basis_points),
        },
        'PUT',
      );
      setMonitors((items) => items.map((item) => (item.id === updated.id ? updated : item)));
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy('');
    }
  }

  const projectNames = new Map(projects.map((project) => [project.id, String(project.name)]));
  return (
    <>
      <SectionHeader
        eyebrow="Production evidence"
        title="Monitors"
        description="Sample production traces against explicit evaluation criteria. Disabled monitors retain their history and stop creating new evaluation jobs."
        actions={
          <>
            <button className="secondary" onClick={() => setRefresh((value) => value + 1)}>
              Refresh
            </button>
            {canWrite && (
              <button onClick={() => setShowForm((visible) => !visible)}>
                {showForm ? 'Close form' : 'Create monitor'}
              </button>
            )}
          </>
        }
      />
      {showForm && canWrite && (
        <form
          className="record-form"
          onSubmit={(event) => {
            void create(event);
          }}
        >
          <h2>Create a production monitor</h2>
          <p>
            Define the retained trace sample and the exact behavior the evaluator should assess.
          </p>
          <div className="form-grid">
            {!projectId && (
              <label>
                Project
                <select name="project_id" required defaultValue="">
                  <option value="">Choose a project…</option>
                  {projects.map((project) => (
                    <option key={project.id} value={project.id}>
                      {String(project.name)}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <label>
              Monitor name
              <input name="name" required maxLength={160} autoFocus />
            </label>
            <label>
              <span id="monitor-sample-label">Sample percentage</span>
              <input
                name="sample_percent"
                type="number"
                min="0"
                max="100"
                step="0.01"
                defaultValue="100"
                required
                aria-labelledby="monitor-sample-label"
                aria-describedby="monitor-sample-hint"
              />
              <span className="hint" id="monitor-sample-hint">
                Stored precisely as basis points; 0.01% equals one basis point.
              </span>
            </label>
            <label className="wide">
              Evaluation criteria
              <textarea name="criteria" rows={4} required maxLength={16000} />
            </label>
          </div>
          <button disabled={busy === 'create' || !projects.length}>
            {busy === 'create' ? 'Creating…' : 'Create monitor'}
          </button>
        </form>
      )}
      {error && <Alert onRetry={() => setRefresh((value) => value + 1)}>{error}</Alert>}
      {loading ? (
        <p className="loading-state" role="status">
          Loading monitors…
        </p>
      ) : !monitors.length && !error ? (
        <EmptyState
          glyph="pulse"
          title="No production monitors configured"
          description="Create a monitor to sample future traces. Existing evidence is unchanged."
          action={
            canWrite && !showForm ? (
              <button onClick={() => setShowForm(true)}>Create monitor</button>
            ) : undefined
          }
        />
      ) : (
        <div className="table-wrap">
          <table>
            <caption className="sr-only">Monitors</caption>
            <thead>
              <tr>
                <th scope="col">Monitor</th>
                <th scope="col">Project</th>
                <th scope="col">Criteria</th>
                <th scope="col">Sample</th>
                <th scope="col">State</th>
                <th scope="col">Created</th>
                <th className="actions">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {monitors.map((monitor) => {
                const enabled = monitor.enabled === true;
                return (
                  <tr key={monitor.id}>
                    <td className="primary-cell">{String(monitor.name)}</td>
                    <td>{projectNames.get(String(monitor.project_id)) || 'Project unavailable'}</td>
                    <td>{String(monitor.criteria)}</td>
                    <td>{(Number(monitor.sample_basis_points) / 100).toLocaleString()}%</td>
                    <td>
                      <StatusBadge value={enabled ? 'enabled' : 'disabled'} />
                    </td>
                    <td>
                      <RelativeTime value={monitor.created_at} />
                    </td>
                    <td className="actions">
                      {canWrite ? (
                        <button
                          className="text-button"
                          disabled={busy === monitor.id}
                          aria-label={`${enabled ? 'Disable' : 'Enable'} ${String(monitor.name)}`}
                          onClick={() => void setEnabled(monitor, !enabled)}
                        >
                          {busy === monitor.id ? 'Saving…' : enabled ? 'Disable' : 'Enable'}
                        </button>
                      ) : (
                        <span className="hint">Read only</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {!loading && monitors.length > 0 && (
        <p className="hint" role="status">
          {monitors.length === 1 ? '1 monitor loaded.' : `${monitors.length} monitors loaded.`}
        </p>
      )}
    </>
  );
}
