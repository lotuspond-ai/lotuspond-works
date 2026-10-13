import { useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { api, ApiError } from '../api.js';
import type { MfaChallenge } from '../lib/mfa.js';

export function MfaCodeInput({ enrollment = false }: { enrollment?: boolean }) {
  const id = useId();
  // Named by its label text alone and described by the hint, so the hint is read once.
  return (
    <label>
      <span id={`${id}-label`}>
        {enrollment ? 'Authenticator code' : 'Authenticator or recovery code'}
      </span>
      <input
        name="code"
        type="text"
        autoComplete="one-time-code"
        autoCapitalize="none"
        spellCheck={false}
        inputMode={enrollment ? 'numeric' : 'text'}
        pattern={enrollment ? '[0-9]{6}' : undefined}
        minLength={6}
        maxLength={64}
        required
        aria-labelledby={`${id}-label`}
        aria-describedby={`${id}-help`}
      />
      <span className="hint" id={`${id}-help`}>
        {enrollment
          ? 'Enter the six-digit code currently shown in your authenticator app.'
          : 'Use the current six-digit app code, or one unused recovery code.'}
      </span>
    </label>
  );
}

export function MfaChallengeForm({
  challenge,
  onComplete,
  onCancel,
}: {
  challenge: MfaChallenge;
  onComplete: (workspace?: string) => Promise<void>;
  onCancel: (message?: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const heading = useRef<HTMLHeadingElement>(null);
  const errorMessage = useRef<HTMLParagraphElement>(null);
  const pending = useRef<AbortController | null>(null);
  useEffect(() => {
    heading.current?.focus();
    return () => pending.current?.abort();
  }, []);
  useEffect(() => {
    if (error) errorMessage.current?.focus();
  }, [error]);
  useEffect(() => {
    if (busy) return;
    const timer = window.setTimeout(
      () => onCancel('This sign-in challenge expired. Sign in again to request a new one.'),
      Math.max(0, Date.parse(challenge.expires_at) - Date.now()),
    );
    return () => window.clearTimeout(timer);
  }, [challenge.expires_at, busy, onCancel]);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const form = event.currentTarget;
    const code = String(new FormData(form).get('code') || '').trim();
    setError('');
    setBusy(true);
    const controller = new AbortController();
    pending.current = controller;
    try {
      const result = await api<{ workspaceId?: string }>(
        '/auth/mfa/challenge/complete',
        undefined,
        { challenge_token: challenge.challenge_token, code },
        'POST',
        controller.signal,
      );
      form.reset();
      if (!controller.signal.aborted) await onComplete(result.workspaceId);
    } catch (reason) {
      if (controller.signal.aborted) return;
      if (reason instanceof ApiError && reason.code === 'invalid_mfa_challenge')
        onCancel('This sign-in challenge is no longer available. Sign in again.');
      else setError((reason as Error).message);
    } finally {
      form.reset();
      if (!controller.signal.aborted) setBusy(false);
    }
  }
  return (
    <>
      <p className="eyebrow">Second sign-in step</p>
      <h1 className="auth-title" ref={heading} tabIndex={-1}>
        Verify your sign-in
      </h1>
      <p>Your password was accepted. Enter a code to finish signing in.</p>
      <p className="hint">
        This request expires at {new Date(challenge.expires_at).toLocaleTimeString()}.
      </p>
      <form
        onSubmit={(event) => {
          void submit(event);
        }}
      >
        <fieldset disabled={busy} className="security-fields">
          <MfaCodeInput />
          {error && (
            <p className="error" role="alert" ref={errorMessage} tabIndex={-1}>
              {error}
            </p>
          )}
          <button>{busy ? 'Verifying…' : 'Verify and sign in'}</button>
          <button type="button" className="secondary" onClick={() => onCancel()}>
            Cancel sign-in
          </button>
        </fieldset>
      </form>
      <p className="hint">
        Lost access to both your app and recovery codes? There is no self-service reset if both are
        lost. Contact your operator for help.
      </p>
    </>
  );
}
