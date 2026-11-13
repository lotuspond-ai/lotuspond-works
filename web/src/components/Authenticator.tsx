import { useEffect, useRef, useState, type FormEvent } from 'react';
import { api, ApiError, type Account } from '../api.js';
import { MfaCodeInput } from './MfaChallenge.js';

type Setup = { secret: string; otpauth_uri: string; expires_at: string };
type Action = 'start' | 'confirm' | 'regenerate' | 'disable';

export function RecoveryCodes({ codes, onSaved }: { codes: string[]; onSaved: () => void }) {
  const [saved, setSaved] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    heading.current?.focus();
  }, []);
  return (
    <section className="recovery-codes" aria-labelledby="recovery-heading">
      <h3 id="recovery-heading" ref={heading} tabIndex={-1}>
        Save your recovery codes
      </h3>
      <p>
        Each code can replace your authenticator once. Store them in a secure password manager or
        another safe place separate from your authenticator. These codes will not be shown again.
      </p>
      <ul className="recovery-code-list">
        {codes.map((code) => (
          <li key={code}>
            <code>{code}</code>
          </li>
        ))}
      </ul>
      <p className="hint">Leaving this page clears this display. Keep these codes private.</p>
      <label className="check-label">
        <input
          type="checkbox"
          checked={saved}
          onChange={(event) => setSaved(event.target.checked)}
        />
        I have saved my recovery codes safely.
      </label>
      <button disabled={!saved} onClick={onSaved}>
        Done — clear codes
      </button>
    </section>
  );
}

export function Authenticator({
  account,
  onChanged,
  onSignedOut,
}: {
  account: Account;
  onChanged: () => Promise<void>;
  onSignedOut: () => Promise<void>;
}) {
  const [action, setAction] = useState<Action | null>(null);
  const [setup, setSetup] = useState<Setup | null>(null);
  const [codes, setCodes] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const heading = useRef<HTMLHeadingElement>(null);
  const errorMessage = useRef<HTMLParagraphElement>(null);
  const formRef = useRef<HTMLFormElement>(null);
  const pending = useRef<AbortController | null>(null);
  const mfa = account.mfa;
  useEffect(() => () => pending.current?.abort(), []);
  useEffect(() => {
    if (error) errorMessage.current?.focus();
  }, [error]);
  useEffect(() => {
    if (action && !busy && !error)
      formRef.current?.querySelector<HTMLInputElement>('input:not([type="hidden"])')?.focus();
  }, [action, busy, error]);
  useEffect(() => {
    if (!setup || busy) return;
    const timer = window.setTimeout(
      () => {
        setSetup(null);
        setAction(null);
        setError('Authenticator setup expired. Start again to get a new setup key.');
      },
      Math.max(0, Date.parse(setup.expires_at) - Date.now()),
    );
    return () => window.clearTimeout(timer);
  }, [setup, busy]);
  useEffect(() => {
    if (!codes.length) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
    };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [codes.length]);

  function choose(next: Action | null) {
    setAction(next);
    setError('');
    setMessage('');
    setSetup(null);
    if (!next) heading.current?.focus();
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!action || busy) return;
    const form = event.currentTarget;
    const fields = new FormData(form);
    const body =
      action === 'confirm'
        ? { code: String(fields.get('code') || '').trim() }
        : {
            current_password: fields.get('current_password'),
            ...(action === 'start' ? {} : { code: String(fields.get('code') || '').trim() }),
          };
    const path =
      action === 'start'
        ? 'enrollment/start'
        : action === 'confirm'
          ? 'enrollment/confirm'
          : action === 'regenerate'
            ? 'recovery-codes/regenerate'
            : 'disable';
    setBusy(true);
    setError('');
    setMessage('');
    const controller = new AbortController();
    pending.current = controller;
    try {
      const result = await api<Setup & { recovery_codes?: string[] }>(
        `/auth/mfa/${path}`,
        undefined,
        body,
        'POST',
        controller.signal,
      );
      form.reset();
      if (controller.signal.aborted) return;
      if (action === 'start') {
        setSetup(result);
        setAction('confirm');
      } else {
        setSetup(null);
        setAction(null);
        setCodes(result.recovery_codes || []);
        setMessage(
          action === 'disable'
            ? 'Authenticator disabled. Your other sessions have been signed out.'
            : action === 'confirm'
              ? 'Authenticator enabled. Your other sessions have been signed out.'
              : 'Recovery codes replaced. Every previous recovery code is now invalid and your other sessions have been signed out.',
        );
        await onChanged();
        if (action === 'disable') heading.current?.focus();
      }
    } catch (reason) {
      if (controller.signal.aborted) return;
      if (reason instanceof ApiError && reason.code === 'mfa_enrollment_unavailable') {
        setSetup(null);
        setAction(null);
      }
      setError((reason as Error).message);
    } finally {
      form.reset();
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  return (
    <section className="authenticator-panel" aria-labelledby="authenticator-heading">
      <h2 id="authenticator-heading" ref={heading} tabIndex={-1}>
        Authenticator
      </h2>
      <p>
        Add a code from an authenticator app as a second sign-in step. Codes work without email
        delivery.
      </p>
      {!mfa ? (
        <p role="status">
          Authenticator availability was not reported. Refresh your account to check.
        </p>
      ) : (
        <>
          <dl className="record-facts">
            <div>
              <dt>Authenticator</dt>
              <dd>{mfa.enabled ? 'Enabled' : 'Not enabled'}</dd>
            </div>
            <div>
              <dt>This session</dt>
              <dd>
                {mfa.session_verified
                  ? 'Verified with a second factor'
                  : 'Not verified with a second factor'}
              </dd>
            </div>
            {mfa.enabled && (
              <div>
                <dt>Unused recovery codes</dt>
                <dd>{mfa.recovery_codes_remaining}</dd>
              </div>
            )}
          </dl>
          {!mfa.available && (
            <p className="notice">
              Authenticator management is unavailable on this installation. Ask your operator to
              configure its encryption key.
            </p>
          )}
          {mfa.required_for_privileged && (
            <p className="notice">
              Platform administration requires an enabled authenticator and a session verified with
              a second factor.
            </p>
          )}
          {mfa.enabled && !mfa.session_verified && (
            <p>
              Sign out and sign in with your authenticator or a recovery code to verify this
              session.
            </p>
          )}
        </>
      )}
      {error && (
        <p className="error" role="alert" ref={errorMessage} tabIndex={-1}>
          {error}
        </p>
      )}
      {message && <p role="status">{message}</p>}
      {codes.length ? (
        <RecoveryCodes
          codes={codes}
          onSaved={() => {
            setCodes([]);
            heading.current?.focus();
          }}
        />
      ) : action ? (
        <form
          className="record-form narrow-form"
          ref={formRef}
          onSubmit={(event) => {
            void submit(event);
          }}
        >
          <fieldset disabled={busy} className="security-fields">
            <legend>
              {action === 'start'
                ? 'Set up your authenticator'
                : action === 'confirm'
                  ? 'Confirm your authenticator'
                  : action === 'regenerate'
                    ? 'Replace recovery codes'
                    : 'Disable authenticator'}
            </legend>
            {setup && action === 'confirm' ? (
              <>
                <p>
                  In your authenticator app, add a time-based account named LotusPond Works using this
                  setup key. Use six digits, a 30-second period, and SHA-1 if your app asks for
                  these settings.
                </p>
                <label>
                  Setup key
                  <input className="setup-key" readOnly value={setup.secret} autoComplete="off" />
                </label>
                <details className="setup-uri">
                  <summary>Manual authenticator URI</summary>
                  <p className="hint">
                    Only transfer this URI to your trusted authenticator app. It contains your setup
                    key.
                  </p>
                  <code>{setup.otpauth_uri}</code>
                </details>
                <p className="hint">
                  Keep the key private. Setup expires at{' '}
                  {new Date(setup.expires_at).toLocaleTimeString()}. Your authenticator must be
                  confirmed before it is enabled.
                </p>
                <MfaCodeInput enrollment />
              </>
            ) : (
              <>
                {action === 'start' && <p>Starting setup signs out your other sessions.</p>}
                {action === 'regenerate' && (
                  <p>
                    All previous recovery codes will stop working immediately and your other
                    sessions will be signed out. Save the replacement codes before leaving this
                    page.
                  </p>
                )}
                {action === 'disable' && (
                  <p>
                    This removes the second sign-in step and invalidates every recovery code. Your
                    other sessions will be signed out.
                  </p>
                )}
                <input type="hidden" autoComplete="username" value={account.user.email} />
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
                {action !== 'start' && <MfaCodeInput />}
              </>
            )}
            <div className="button-row">
              <button>
                {busy
                  ? 'Saving…'
                  : action === 'start'
                    ? 'Continue setup'
                    : action === 'confirm'
                      ? 'Enable authenticator'
                      : action === 'regenerate'
                        ? 'Replace recovery codes'
                        : 'Disable authenticator'}
              </button>
              <button type="button" className="secondary" onClick={() => choose(null)}>
                Cancel
              </button>
            </div>
          </fieldset>
        </form>
      ) : (
        mfa && (
          <div className="button-row">
            {!mfa.enabled ? (
              <button disabled={!mfa.available || busy} onClick={() => choose('start')}>
                Set up authenticator
              </button>
            ) : (
              <>
                <button
                  className="secondary"
                  disabled={!mfa.available || busy}
                  onClick={() => choose('regenerate')}
                >
                  Replace recovery codes
                </button>
                {!mfa.required_for_privileged && (
                  <button
                    className="secondary"
                    disabled={!mfa.available || busy}
                    onClick={() => choose('disable')}
                  >
                    Disable authenticator
                  </button>
                )}
                {!mfa.session_verified && (
                  <button
                    className="secondary"
                    disabled={busy}
                    onClick={async () => {
                      setBusy(true);
                      setError('');
                      try {
                        await api('/auth/logout', undefined, {});
                        await onSignedOut();
                        location.hash = 'login';
                      } catch (reason) {
                        setError((reason as Error).message);
                      } finally {
                        setBusy(false);
                      }
                    }}
                  >
                    Sign out to verify
                  </button>
                )}
              </>
            )}
          </div>
        )
      )}
    </section>
  );
}
