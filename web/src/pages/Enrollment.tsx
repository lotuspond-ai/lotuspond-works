import { useEffect, useRef, useState, type FormEvent } from 'react';
import { api, ApiError, type Account } from '../api.js';
import { displayValue } from '../components/ui.js';
import type { IdentityLinks } from '../lib/identityLinks.js';
import { documentTitle, useDocumentTitle } from '../lib/title.js';

export type EnrollmentDetails = {
  email: string;
  name: string;
  expires_at: string;
  organization: { id: string; name: string } | null;
  projects: { id: string; name: string }[];
  identity_verification_source: 'operator_attested';
};

export function EnrollmentForm({
  details,
  busy,
  onSubmit,
}: {
  details: EnrollmentDetails;
  busy: boolean;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
}) {
  return (
    <form onSubmit={onSubmit}>
      <input type="hidden" name="username" autoComplete="username" value={details.email} />
      <dl className="record-facts">
        <div>
          <dt>Name</dt>
          <dd>{details.name}</dd>
        </div>
        <div>
          <dt>Email</dt>
          <dd>{details.email}</dd>
        </div>
        <div>
          <dt>Link expires</dt>
          <dd>{displayValue(details.expires_at)}</dd>
        </div>
        <div>
          <dt>Identity source</dt>
          <dd>Operator attested</dd>
        </div>
      </dl>
      <p>
        An operator attested to your identity outside Lotuspond. This does not verify ownership of
        your mailbox. Contact the operator if these account details are incorrect.
      </p>
      {details.organization ? (
        <div className="notice">
          <h2>Provisioned organization</h2>
          <p>{details.organization.name}</p>
          <p>The operator has provisioned this organization for your account.</p>
          {!!details.projects.length && (
            <>
              <h3>Provisioned projects</h3>
              <ul>
                {details.projects.map((project) => (
                  <li key={project.id}>{project.name}</li>
                ))}
              </ul>
            </>
          )}
        </div>
      ) : (
        <p>
          Your account will use a personal workspace. No organization will be created by this form.
        </p>
      )}
      <label>
        Password
        <input
          name="password"
          type="password"
          autoComplete="new-password"
          required
          minLength={12}
          maxLength={256}
          aria-describedby="enrollment-password-rules"
        />
      </label>
      <label>
        Confirm password
        <input
          name="confirmation"
          type="password"
          autoComplete="new-password"
          required
          minLength={12}
          maxLength={256}
          aria-describedby="enrollment-password-rules"
        />
      </label>
      <p className="hint" id="enrollment-password-rules">
        Choose a password with at least 12 characters. The enrollment link can be used once.
      </p>
      <button disabled={busy}>{busy ? 'Activating account…' : 'Activate account'}</button>
    </form>
  );
}

export function Enrollment({
  links,
  account,
  onRefresh,
  onCompleted,
}: {
  links: IdentityLinks;
  account: Account | null;
  onRefresh: () => Promise<void>;
  onCompleted: (workspace: string) => Promise<void>;
}) {
  const [details, setDetails] = useState<EnrollmentDetails | null>(null);
  const [completed, setCompleted] = useState<EnrollmentDetails | null>(null);
  useDocumentTitle(documentTitle(completed ? 'Your account is active' : 'Set up your account'));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [revision, setRevision] = useState(0);
  const heading = useRef<HTMLHeadingElement>(null);
  const errorMessage = useRef<HTMLDivElement>(null);
  const token = links.token('enroll');
  useEffect(() => {
    heading.current?.focus();
  }, [completed]);
  useEffect(() => {
    if (error) errorMessage.current?.focus();
  }, [error]);
  useEffect(() => {
    if (!token || completed) return;
    const controller = new AbortController();
    setDetails(null);
    setError('');
    void api<EnrollmentDetails>(
      '/auth/offline-enrollment/inspect',
      undefined,
      { token },
      'POST',
      controller.signal,
    )
      .then((value) => {
        if (!controller.signal.aborted) setDetails(value);
      })
      .catch((reason: Error) => {
        if (controller.signal.aborted) return;
        if (
          reason instanceof ApiError &&
          ['invalid_enrollment', 'enrollment_expired', 'enrollment_unavailable'].includes(
            reason.code || '',
          )
        )
          links.clear('enroll');
        setError(reason.message);
      });
    return () => controller.abort();
  }, [token, links, revision, completed]);

  async function activate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!details || account) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    setError('');
    if (data.get('password') !== data.get('confirmation')) {
      setError('The passwords do not match.');
      return;
    }
    const activeToken = links.token('enroll');
    if (!activeToken) {
      setError('Reopen the complete enrollment link from your operator.');
      return;
    }
    setBusy(true);
    try {
      const result = await api<{ workspaceId: string }>(
        '/auth/offline-enrollment/complete',
        undefined,
        { token: activeToken, password: data.get('password') },
      );
      form.reset();
      setCompleted(details);
      links.clear('enroll');
      await onCompleted(result.workspaceId);
    } catch (reason) {
      if (
        reason instanceof ApiError &&
        ['invalid_enrollment', 'enrollment_expired', 'enrollment_unavailable'].includes(
          reason.code || '',
        )
      )
        links.clear('enroll');
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <p className="eyebrow">Operator enrollment</p>
      <h1 ref={heading} tabIndex={-1}>
        {completed ? 'Your account is active' : 'Set up your account'}
      </h1>
      {error && (
        <div className="error" role="alert" ref={errorMessage} tabIndex={-1}>
          {error}
        </div>
      )}
      {completed ? (
        <div role="status">
          <p>
            {completed.name}, your account is active with operator-attested identity. Mailbox
            verification remains separate. Sign in with the password you chose to open your
            workspace.
          </p>
          <div className="inline-actions">
            <a className="text-button" href="#login">
              Sign in
            </a>
          </div>
        </div>
      ) : !token ? (
        <p>
          This enrollment link is missing, expired, or no longer available in this tab. Reopen the
          complete link supplied by your operator, or ask for a new link.
        </p>
      ) : !details ? (
        error ? (
          <button className="secondary" onClick={() => setRevision((value) => value + 1)}>
            Try again
          </button>
        ) : (
          <p role="status">Checking the enrollment link…</p>
        )
      ) : account ? (
        <>
          <p>
            You are signed in as <strong>{account.user.email}</strong>. Sign out before activating
            the operator-provisioned account for <strong>{details.email}</strong>.
          </p>
          <button
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
            {busy ? 'Signing out…' : 'Sign out to continue'}
          </button>
        </>
      ) : (
        <EnrollmentForm
          details={details}
          busy={busy}
          onSubmit={(event) => {
            void activate(event);
          }}
        />
      )}
    </>
  );
}
