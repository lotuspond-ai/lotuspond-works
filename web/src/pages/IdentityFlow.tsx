import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { api, ApiError, type Account } from '../api.js';
import { Alert, StatusBadge } from '../components/ui.js';
import type { IdentityLinks } from '../lib/identityLinks.js';
import { organizationDraft } from '../lib/registration.js';
import { identityLabel, identityVerified } from '../lib/identityStatus.js';
import { documentTitle, useDocumentTitle } from '../lib/title.js';

export function IdentityFrame({ children, signedIn }: { children: ReactNode; signedIn: boolean }) {
  return (
    <div className="welcome identity-page">
      <header>
        <a className="wordmark" href="#releases">
          Lotuspond
          <span className="wordmark-dot" />
        </a>
        <a className="text-button" href={signedIn ? '#releases' : '#login'}>
          {signedIn ? 'Open workspace' : 'Sign in'}
        </a>
      </header>
      <main className="auth-panel identity-panel">{children}</main>
    </div>
  );
}

export function VerificationCard({
  account,
  onRefresh,
}: {
  account: Account;
  onRefresh: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const mailDisabled = account.capabilities?.mail_enabled === false;
  const offlineInstallation = account.capabilities?.offline_enrollment_enabled === true;
  async function request() {
    setBusy(true);
    setError('');
    setMessage('');
    try {
      await api('/auth/verification/request', undefined, {});
      setMessage(
        'Verification request accepted. Check your inbox for a link; delivery is not confirmed.',
      );
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="panel" aria-labelledby="email-verification-heading">
      <h2 id="email-verification-heading">Identity verification</h2>
      <p>
        {account.user.email} <StatusBadge value={identityLabel(account.user)} />
      </p>
      {account.user.email_verified ? (
        <p>Your mailbox is verified.</p>
      ) : (
        <>
          {identityVerified(account.user) ? (
            <p>
              {account.user.identity_verification_source === 'operator_attested'
                ? 'An operator attested to your identity. Your mailbox has not been verified.'
                : 'Your identity is verified, but its source was not recorded. Your mailbox has not been verified.'}{' '}
              Mailbox verification is a separate ownership check.
            </p>
          ) : (
            <p>Verify your mailbox before organization access or accepting invitations.</p>
          )}
          {mailDisabled && (
            <p role="status">
              {offlineInstallation
                ? 'Mailbox verification is unavailable in this isolated installation. Your operator manages account enrollment.'
                : 'Email delivery is disabled on this installation. Ask the operator to configure mail before requesting verification.'}
            </p>
          )}
          <div className="inline-actions">
            <button
              disabled={busy || mailDisabled}
              onClick={() => {
                void request();
              }}
            >
              Request verification email
            </button>
            <button
              className="secondary"
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                setError('');
                try {
                  await onRefresh();
                } catch (reason) {
                  setError((reason as Error).message);
                } finally {
                  setBusy(false);
                }
              }}
            >
              Refresh verification status
            </button>
          </div>
        </>
      )}
      {message && <p role="status">{message}</p>}
      {error && <Alert>{error}</Alert>}
      {organizationDraft(account.user.email) && (
        <p className="hint">
          Your organization name is saved as a draft.{' '}
          {identityVerified(account.user) ? 'Open' : 'After verifying your identity, open'}{' '}
          <a className="text-button" href="#workspaces">
            Your workspaces
          </a>{' '}
          to create it.
        </p>
      )}
    </section>
  );
}

export function IdentityFlow({
  page,
  links,
  account,
  onRefresh,
  onAccepted,
}: {
  page: 'verify-email' | 'forgot-password' | 'reset-password' | 'accept-invitation';
  links: IdentityLinks;
  account: Account | null;
  onRefresh: () => Promise<void>;
  onAccepted: (workspace: string) => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [done, setDone] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const errorMessage = useRef<HTMLDivElement>(null);
  useEffect(() => {
    heading.current?.focus();
  }, []);
  useEffect(() => {
    if (error) errorMessage.current?.focus();
  }, [error]);
  const token = page === 'forgot-password' ? undefined : links.token(page);
  const title =
    page === 'verify-email'
      ? 'Verify your email'
      : page === 'accept-invitation'
        ? 'Join your organization'
        : page === 'forgot-password'
          ? 'Recover your account'
          : 'Choose a new password';
  useDocumentTitle(documentTitle(title));

  async function submit(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    const form = event?.currentTarget;
    const fields = form ? new FormData(form) : null;
    setError('');
    if (page === 'reset-password' && fields?.get('new_password') !== fields?.get('confirmation')) {
      setError('The new passwords do not match.');
      return;
    }
    setBusy(true);
    try {
      const activeToken = page === 'forgot-password' ? undefined : links.token(page);
      if (page === 'forgot-password') {
        await api('/auth/password/reset/request', undefined, { email: fields?.get('email') });
      } else if (!activeToken) {
        setError('Reopen the complete link from your email.');
        return;
      } else if (page === 'reset-password') {
        await api('/auth/password/reset/confirm', undefined, {
          token: activeToken,
          new_password: fields?.get('new_password'),
        });
        links.clear(page);
        form?.reset();
        await onRefresh();
      } else if (page === 'verify-email') {
        await api('/auth/verification/confirm', undefined, { token: activeToken });
        links.clear(page);
        await onRefresh();
      } else {
        const result = await api<{ workspace_id: string }>('/invitations/accept', undefined, {
          token: activeToken,
        });
        links.clear(page);
        await onAccepted(result.workspace_id);
      }
      setDone(true);
    } catch (reason) {
      if (
        reason instanceof ApiError &&
        reason.code === 'invalid_token' &&
        (page === 'verify-email' || page === 'reset-password')
      )
        links.clear(page);
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <p className="eyebrow">Account access</p>
      <h1 ref={heading} tabIndex={-1}>
        {title}
      </h1>
      {error && (
        <div className="error" role="alert" ref={errorMessage} tabIndex={-1}>
          {error}
        </div>
      )}
      {done ? (
        <div role="status">
          <p>
            {page === 'forgot-password'
              ? 'If this address is eligible and email is configured, a recovery request has been accepted. Check your inbox. Delivery is not confirmed.'
              : page === 'reset-password'
                ? 'Your password has been changed and existing sessions have been signed out. Sign in with your new password.'
                : page === 'verify-email'
                  ? 'The email address associated with this link is verified.'
                  : 'You have joined the organization.'}
          </p>
          {links.token('accept-invitation') ? (
            <a className="text-button" href="#accept-invitation">
              Continue to your invitation
            </a>
          ) : (
            <a className="text-button" href={account ? '#settings' : '#login'}>
              {account ? 'Open settings' : 'Sign in'}
            </a>
          )}
          {page === 'verify-email' && !links.token('accept-invitation') && (
            <p className="hint">
              If you were invited in another tab, return to that tab or reopen the invitation link.
            </p>
          )}
        </div>
      ) : page !== 'forgot-password' && !token ? (
        <>
          <p>
            This link is missing, invalid, or no longer available in this tab. Reopen the complete
            link from your email.
          </p>
          <a
            className="text-button"
            href={page === 'reset-password' ? '#forgot-password' : account ? '#settings' : '#login'}
          >
            {page === 'reset-password'
              ? 'Request a new recovery link'
              : account
                ? 'Open settings'
                : 'Sign in'}
          </a>
        </>
      ) : page === 'forgot-password' ? (
        <form
          onSubmit={(event) => {
            void submit(event);
          }}
        >
          <p>Enter your account email to request a password recovery link.</p>
          <label>
            Email
            <input name="email" type="email" autoComplete="email" maxLength={320} required />
          </label>
          <button disabled={busy}>{busy ? 'Requesting…' : 'Request recovery link'}</button>
          <a className="text-button" href="#login">
            Back to sign in
          </a>
        </form>
      ) : page === 'reset-password' ? (
        <form
          onSubmit={(event) => {
            void submit(event);
          }}
        >
          <p id="reset-password-rules">
            Use at least 12 characters. This signs out all existing sessions.
          </p>
          <label>
            New password
            <input
              name="new_password"
              type="password"
              autoComplete="new-password"
              minLength={12}
              maxLength={256}
              required
              aria-describedby="reset-password-rules"
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
              aria-describedby="reset-password-rules"
            />
          </label>
          <button disabled={busy}>{busy ? 'Changing password…' : 'Change password'}</button>
        </form>
      ) : page === 'verify-email' ? (
        <>
          <p>Confirm this email link to verify ownership of your mailbox.</p>
          <button
            disabled={busy}
            onClick={() => {
              void submit();
            }}
          >
            {busy ? 'Verifying…' : 'Verify email'}
          </button>
        </>
      ) : account ? (
        <>
          <p>
            Accept the invitation using <strong>{account.user.email}</strong>. The invitation must
            match this mailbox-verified email address.
          </p>
          {error && (
            <p className="hint">
              If this invitation was sent to a different address, switch accounts. Otherwise ask the
              organization administrator for a new invitation.
            </p>
          )}
          {!account.user.email_verified ? (
            <>
              <VerificationCard account={account} onRefresh={onRefresh} />
              <p className="hint">
                Email invitations require mailbox verification, including for operator-attested
                accounts. After verifying, return here and refresh your verification status. Your
                invitation stays in this tab for up to one hour.
              </p>
            </>
          ) : (
            <button
              disabled={busy}
              onClick={() => {
                void submit();
              }}
            >
              {busy ? 'Joining…' : 'Accept invitation'}
            </button>
          )}
          <button
            className="text-button"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              setError('');
              try {
                await api('/auth/logout', undefined, {});
                await onRefresh();
              } catch (reason) {
                setError((reason as Error).message);
              } finally {
                setBusy(false);
              }
            }}
          >
            Use a different account
          </button>
        </>
      ) : (
        <p>
          <a className="text-button" href="#login">
            Sign in to accept this invitation
          </a>
        </p>
      )}
    </>
  );
}
