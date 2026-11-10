import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { api, type Row } from '../api.js';
import { Alert, EmptyState, RelativeTime, SectionHeader, StatusBadge } from '../components/ui.js';

type Overview = {
  users: number;
  workspaces: number;
  projects: number;
  runs: number;
  pending_approvals: number;
};
export function Admin() {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [workspaces, setWorkspaces] = useState<Row[]>([]);
  const [users, setUsers] = useState<Row[]>([]);
  const [tab, setTab] = useState<'overview' | 'workspaces' | 'users'>('overview');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [selected, setSelected] = useState<Row | null>(null);
  const [busy, setBusy] = useState(false);
  const [query, setQuery] = useState('');
  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [summary, workspacesResult, usersResult] = await Promise.all([
        api<Overview>('/admin/overview'),
        api<{ items: Row[] }>('/admin/workspaces'),
        api<{ items: Row[] }>('/admin/users'),
      ]);
      setOverview(summary);
      setWorkspaces(workspacesResult.items);
      setUsers(usersResult.items);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);
  async function changeStatus(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected) return;
    setBusy(true);
    setError('');
    const form = new FormData(event.currentTarget);
    const status = selected.status === 'suspended' ? 'active' : 'suspended';
    try {
      await api(
        `/admin/workspaces/${encodeURIComponent(selected.id)}`,
        undefined,
        { status, reason: String(form.get('reason')).trim() },
        'PATCH',
      );
      setNotice(
        `${String(selected.name)} is now ${status}. The change has been recorded in the audit log.`,
      );
      setSelected(null);
      await load();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const loaded = tab === 'users' ? users : workspaces;
  const records = loaded.filter((row) =>
    [row.name, row.email, row.kind, row.status].some((value) =>
      String(value || '')
        .toLowerCase()
        .includes(query.toLowerCase()),
    ),
  );
  return (
    <>
      <SectionHeader
        eyebrow="Platform administration"
        title="Administration"
        description="Manage platform access and review recorded workspace activity. Administrative changes require a reason and are audited."
        actions={
          <button
            className="secondary"
            disabled={loading}
            onClick={() => {
              void load();
            }}
          >
            Refresh
          </button>
        }
      />
      <nav className="tab-bar" aria-label="Administration sections">
        {(['overview', 'workspaces', 'users'] as const).map((item) => (
          <button
            key={item}
            aria-current={tab === item ? 'page' : undefined}
            onClick={() => {
              setTab(item);
              setQuery('');
            }}
          >
            {item === 'overview' ? 'Overview' : item === 'workspaces' ? 'Workspaces' : 'Users'}
          </button>
        ))}
      </nav>
      {error && (
        <Alert
          onRetry={() => {
            void load();
          }}
        >
          {error}
        </Alert>
      )}
      {notice && (
        <p className="success-notice" role="status">
          {notice}
        </p>
      )}
      {loading ? (
        <p className="loading-state" role="status">
          Loading platform records…
        </p>
      ) : !error && tab === 'overview' && overview ? (
        <>
          <dl className="metric-strip">
            {Object.entries(overview).map(([key, value]) => (
              <div key={key}>
                <dt>{key.replaceAll('_', ' ')}</dt>
                <dd>{value.toLocaleString()}</dd>
              </div>
            ))}
          </dl>
          <section className="support-note">
            <h2>Workspace access</h2>
            <p>
              Suspension blocks workspace access. Restore access after resolving the underlying
              issue. Review the affected workspace before changing its status.
            </p>
            <button className="secondary" onClick={() => setTab('workspaces')}>
              Manage workspaces
            </button>
          </section>
        </>
      ) : !error && tab !== 'overview' ? (
        <>
          <div className="toolbar">
            <label className="search">
              <span className="sr-only">Filter {tab}</span>
              <input
                placeholder={`Filter loaded ${tab}…`}
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
            </label>
            {/* Always present, so a filter's new count is announced as it changes. */}
            <span className="toolbar-count" role="status">
              {records.length === loaded.length
                ? `${loaded.length} ${loaded.length === 1 ? 'record' : 'records'}`
                : `${records.length} of ${loaded.length} records`}
            </span>
          </div>
          {selected && (
            <form
              className="record-form narrow-form"
              onSubmit={(event) => {
                void changeStatus(event);
              }}
            >
              <h2>
                {selected.status === 'suspended' ? 'Restore' : 'Suspend'} {String(selected.name)}
              </h2>
              <p>
                {selected.status === 'suspended'
                  ? 'Members will regain workspace access.'
                  : 'Members and applications will lose access to this workspace.'}
              </p>
              <label>
                Reason
                <textarea
                  name="reason"
                  required
                  minLength={10}
                  maxLength={2000}
                  rows={3}
                  autoFocus
                />
              </label>
              <div className="button-row">
                <button disabled={busy}>{busy ? 'Saving…' : 'Confirm change'}</button>
                <button
                  type="button"
                  className="secondary"
                  disabled={busy}
                  onClick={() => setSelected(null)}
                >
                  Cancel
                </button>
              </div>
            </form>
          )}
          {!records.length ? (
            <EmptyState
              glyph={query ? undefined : 'network'}
              title={query ? 'No matching records' : `No ${tab} recorded`}
              description={
                query
                  ? 'Try another filter, or clear it to see every loaded record.'
                  : 'Refresh after accounts or workspaces have been created.'
              }
              action={
                query ? (
                  <button className="secondary" onClick={() => setQuery('')}>
                    Clear filter
                  </button>
                ) : undefined
              }
            />
          ) : (
            <div className="table-wrap">
              <table>
                <caption className="sr-only">{tab === 'users' ? 'Users' : 'Workspaces'}</caption>
                <thead>
                  <tr>
                    <th scope="col">Name</th>
                    <th scope="col">{tab === 'users' ? 'Email' : 'Type'}</th>
                    <th scope="col">{tab === 'users' ? 'Platform role' : 'Status'}</th>
                    <th scope="col">Created</th>
                    {tab === 'workspaces' && (
                      <th className="actions">
                        <span className="sr-only">Actions</span>
                      </th>
                    )}
                  </tr>
                </thead>
                <tbody>
                  {records.map((row) => (
                    <tr key={row.id}>
                      <td className="primary-cell">{String(row.name)}</td>
                      <td>{String((tab === 'users' ? row.email : row.kind) || 'Not recorded')}</td>
                      <td>
                        {tab === 'users' ? (
                          row.is_platform_admin ? (
                            'Platform admin'
                          ) : (
                            'Member'
                          )
                        ) : (
                          <StatusBadge value={row.status} />
                        )}
                      </td>
                      <td>
                        <RelativeTime value={row.created_at} />
                      </td>
                      {tab === 'workspaces' && (
                        <td className="actions">
                          <button
                            className={`text-button ${row.status !== 'suspended' ? 'danger' : ''}`}
                            onClick={() => setSelected(row)}
                          >
                            {row.status === 'suspended' ? 'Restore access' : 'Suspend'}
                          </button>
                        </td>
                      )}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      ) : null}
    </>
  );
}
