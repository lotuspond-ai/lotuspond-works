import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { api, type Account, type Workspace } from '../api.js';
import {
  Alert,
  EmptyState,
  RelativeTime,
  SectionHeader,
  StatusBadge,
  humanize,
} from '../components/ui.js';

type Invitation = {
  id: string;
  email: string;
  role: string;
  status: 'pending' | 'accepted' | 'revoked' | 'expired';
  expires_at: string;
  created_at: string;
};
export function Invitations({ workspace, account }: { workspace: Workspace; account: Account }) {
  const [items, setItems] = useState<Invitation[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const permitted =
    workspace.kind === 'organization' && ['owner', 'admin'].includes(workspace.role);
  const mailDisabled = account.capabilities?.mail_enabled === false;
  const offlineInstallation = account.capabilities?.offline_enrollment_enabled === true;
  const load = useCallback(
    async (signal?: AbortSignal) => {
      const data = await api<{ items: Invitation[] }>(
        '/invitations',
        workspace.id,
        undefined,
        'GET',
        signal,
      );
      if (!signal?.aborted) setItems(data.items);
    },
    [workspace.id],
  );
  useEffect(() => {
    if (!permitted) {
      setLoading(false);
      return;
    }
    const controller = new AbortController();
    void load(controller.signal)
      .catch((reason: Error) => {
        if (!controller.signal.aborted) setError(reason.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [load, permitted]);
  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    setBusy(true);
    setError('');
    setMessage('');
    try {
      await api('/invitations', workspace.id, {
        email: data.get('email'),
        role: data.get('role'),
        days: Number(data.get('days')),
      });
      form.reset();
      setMessage('Invitation created and email queued. Delivery is not confirmed.');
      await load();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <SectionHeader
        eyebrow="Workspace"
        title="Invitations"
        description="Invite a person to your organization with the access they need. Membership starts only after they verify their email and accept."
      />
      {!permitted ? (
        <EmptyState
          title="Organization administrator access required"
          description="Choose an organization where you are an owner or administrator to manage invitations."
        />
      ) : (
        <>
          {error && <Alert>{error}</Alert>}
          {message && <p role="status">{message}</p>}
          {mailDisabled && (
            <p className="notice" role="status">
              {offlineInstallation
                ? 'Email invitations are unavailable in this isolated installation. Ask your operator to enroll the person, then add their account through Members.'
                : 'Email delivery is disabled on this installation. An operator must configure mail before you can send invitations.'}
            </p>
          )}
          <form
            className="record-form narrow-form"
            onSubmit={(event) => {
              void create(event);
            }}
          >
            <h2>Invite a teammate</h2>
            <label>
              Email
              <input name="email" type="email" required maxLength={320} autoComplete="off" />
            </label>
            <label>
              Role
              <select name="role" defaultValue="viewer">
                <option value="viewer">Viewer — read workspace records</option>
                <option value="editor">Editor — create and evaluate</option>
                <option value="admin">Administrator — manage access and policy</option>
              </select>
            </label>
            <label>
              Expires after (days)
              <input name="days" type="number" min={1} max={30} defaultValue={7} required />
            </label>
            <button disabled={busy || mailDisabled}>{busy ? 'Saving…' : 'Send invitation'}</button>
          </form>
          <h2>Invitations for {workspace.name}</h2>
          {loading ? (
            <p role="status">Loading invitations…</p>
          ) : items.length ? (
            <div className="table-wrap">
              <table>
                <caption className="sr-only">Invitations</caption>
                <thead>
                  <tr>
                    <th scope="col">Email</th>
                    <th scope="col">Role</th>
                    <th scope="col">Status</th>
                    <th scope="col">Expires</th>
                    <th className="actions">
                      <span className="sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((item) => (
                    <tr key={item.id}>
                      <td className="primary-cell">{item.email}</td>
                      <td>{humanize(item.role)}</td>
                      <td>
                        <StatusBadge value={item.status} />
                      </td>
                      <td>
                        <RelativeTime value={item.expires_at} />
                      </td>
                      <td className="actions">
                        {item.status === 'pending' ? (
                          <button
                            className="text-button danger"
                            disabled={busy}
                            onClick={async () => {
                              setBusy(true);
                              setError('');
                              setMessage('');
                              try {
                                await api(`/invitations/${item.id}/revoke`, workspace.id, {});
                                setMessage('Invitation revoked.');
                                await load();
                              } catch (reason) {
                                setError((reason as Error).message);
                              } finally {
                                setBusy(false);
                              }
                            }}
                            aria-label={`Revoke invitation for ${item.email}`}
                          >
                            Revoke
                          </button>
                        ) : (
                          <span className="hint">No action</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            !error && (
              <EmptyState
                glyph="network"
                title="No invitations yet"
                description="Invitations appear here after they have been created."
              />
            )
          )}
        </>
      )}
    </>
  );
}
