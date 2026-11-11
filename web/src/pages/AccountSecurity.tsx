import { useCallback, useEffect, useState, type FormEvent } from 'react';
import { api, type Account } from '../api.js';
import { Alert, SectionHeader } from '../components/ui.js';
import { VerificationCard } from './IdentityFlow.js';
import { Authenticator } from '../components/Authenticator.js';

type Session = { id: string; created_at: string; expires_at: string; current: boolean };

export function AccountSecurity({
  account,
  onSignedOut,
  onRefresh,
}: {
  account: Account;
  onSignedOut: () => Promise<void>;
  onRefresh: () => Promise<void>;
}) {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const load = useCallback(async (signal?: AbortSignal) => {
    const result = await api<{ items: Session[] }>(
      '/auth/sessions',
      undefined,
      undefined,
      'GET',
      signal,
    );
    if (!signal?.aborted) setSessions(result.items);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal)
      .catch((reason: Error) => {
        if (!controller.signal.aborted) setError(reason.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [load]);

  async function revoke(session: Session) {
    setBusy(true);
    setError('');
    setMessage('');
    try {
      const result = await api<{ signed_out: boolean }>(
        `/auth/sessions/${session.id}`,
        undefined,
        {},
        'DELETE',
      );
      if (result.signed_out) await onSignedOut();
      else {
        setMessage('Session signed out.');
        await load();
      }
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function changePassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError('');
    setMessage('');
    const form = event.currentTarget;
    const data = new FormData(form);
    if (data.get('new_password') !== data.get('confirmation')) {
      setError('The new passwords do not match.');
      return;
    }
    setBusy(true);
    try {
      await api('/auth/password', undefined, {
        current_password: data.get('current_password'),
        new_password: data.get('new_password'),
      });
      form.reset();
      setMessage('Password changed. Your other sessions have been signed out.');
      await load();
      await onRefresh();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <SectionHeader
        eyebrow="Account"
        title="Account security"
        description="Manage access to your account across all your workspaces."
      />
      <VerificationCard account={account} onRefresh={onRefresh} />
      <Authenticator
        account={account}
        onSignedOut={onSignedOut}
        onChanged={async () => {
          await onRefresh();
          await load();
        }}
      />
      {error && <Alert>{error}</Alert>}
      {message && <p role="status">{message}</p>}
      <form
        className="record-form narrow-form"
        onSubmit={(event) => {
          void changePassword(event);
        }}
      >
        <h2>Change password</h2>
        <p id="password-rules">
          Use at least 12 characters. Changing your password signs out every other session.
        </p>
        {account.capabilities?.mail_enabled && (
          <p className="hint">
            LotusPond Works emails you whenever your password or authenticator changes. The message
            carries no link and no code, so treat any that does as suspicious.
          </p>
        )}
        <label>
          Current password
          <input
            name="current_password"
            type="password"
            autoComplete="current-password"
            maxLength={256}
            required
          />
        </label>
        <label>
          New password
          <input
            name="new_password"
            type="password"
            autoComplete="new-password"
            minLength={12}
            maxLength={256}
            required
            aria-describedby="password-rules"
          />
        </label>
        <label>
          Confirm new password
          <input
            name="confirmation"
            type="password"
            autoComplete="new-password"
            minLength={12}
            maxLength={256}
            required
            aria-describedby="password-rules"
          />
        </label>
        <button disabled={busy}>{busy ? 'Updating…' : 'Change password'}</button>
      </form>
      <section aria-labelledby="sessions-heading">
        <h2 id="sessions-heading">Active sessions</h2>
        <p>
          Sign out sessions you no longer use. Sessions expire automatically; your account keeps at
          most 20 active sessions.
        </p>
        {loading ? (
          <p role="status">Loading sessions…</p>
        ) : (
          <div className="table-wrap">
            <table>
              <caption className="sr-only">Active sessions</caption>
              <thead>
                <tr>
                  <th scope="col">Session</th>
                  <th scope="col">Signed in</th>
                  <th scope="col">Expires</th>
                  <th scope="col">Action</th>
                </tr>
              </thead>
              <tbody>
                {sessions.map((session) => (
                  <tr key={session.id}>
                    <td>{session.current ? 'This session' : 'Other session'}</td>
                    <td>
                      <time dateTime={session.created_at}>
                        {new Date(session.created_at).toLocaleString()}
                      </time>
                    </td>
                    <td>
                      <time dateTime={session.expires_at}>
                        {new Date(session.expires_at).toLocaleString()}
                      </time>
                    </td>
                    <td>
                      <button
                        className="secondary"
                        disabled={busy}
                        onClick={() => {
                          void revoke(session);
                        }}
                        aria-label={
                          session.current
                            ? 'Sign out this session'
                            : `Sign out session from ${new Date(session.created_at).toLocaleString()}`
                        }
                      >
                        {session.current ? 'Sign out here' : 'Sign out'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}
