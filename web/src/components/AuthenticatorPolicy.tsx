import { useState } from 'react';
import { api } from '../api.js';
import {
  authenticatorPolicyView,
  type AuthenticatorPolicy as Policy,
} from '../lib/authenticatorPolicy.js';
import { StatusBadge } from './ui.js';

export function AuthenticatorPolicy({
  workspace,
  policy,
  canEdit,
  enrolled,
  onChanged,
}: {
  workspace: string;
  policy: Policy;
  canEdit: boolean;
  enrolled: boolean;
  onChanged: (next: Policy) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const view = authenticatorPolicyView(policy, { canEdit, enrolled });
  async function change(required: boolean) {
    setBusy(true);
    setError('');
    try {
      const result = await api<{ require_mfa: boolean; members_without_authenticator: number }>(
        '/settings/authenticator-policy',
        workspace,
        { require_mfa: required },
      );
      onChanged({
        ...policy,
        require_mfa: result.require_mfa,
        members_without_authenticator: result.members_without_authenticator,
      });
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="panel">
      <h2>Authenticator policy</h2>
      <p>
        <StatusBadge
          value={
            view.state === 'required'
              ? 'required'
              : view.state === 'optional'
                ? 'optional'
                : 'unavailable'
          }
        />
      </p>
      <p>{view.summary}</p>
      <p className="hint">{view.guidance}</p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {view.action && (
        <button
          className={view.action === 'enable' ? undefined : 'secondary'}
          disabled={busy}
          onClick={() => {
            void change(view.action === 'enable');
          }}
        >
          {busy ? 'Saving…' : view.actionLabel}
        </button>
      )}
      {!view.action && view.state !== 'personal' && (
        <p className="hint">
          Your own authenticator is managed in{' '}
          <a className="text-button" href="#account-security">
            Account security
          </a>
          .
        </p>
      )}
    </section>
  );
}
