import { useState, type FormEvent } from 'react';
import { api, type Account } from '../api.js';
import { Alert, SectionHeader } from '../components/ui.js';
import { organizationDraft, clearOrganizationDraft } from '../lib/registration.js';
import { identityVerified } from '../lib/identityStatus.js';

export function Workspaces({
  account,
  active,
  onSelect,
  onCreated,
}: {
  account: Account;
  active: string;
  onSelect: (id: string) => void;
  onCreated: () => Promise<void>;
}) {
  const [showForm, setShowForm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const draft = organizationDraft(account.user.email);
  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError('');
    const form = new FormData(event.currentTarget);
    try {
      const result = await api<{ id?: string; workspace?: { id: string } }>(
        '/organizations',
        undefined,
        { name: String(form.get('name')).trim() },
      );
      await onCreated();
      const id = result.id || result.workspace?.id;
      if (id) onSelect(id);
      clearOrganizationDraft();
      setShowForm(false);
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
        title="Your workspaces"
        description="Keep individual work in your personal workspace. Create an organization for a shared team, projects, and access policies."
        actions={
          <button onClick={() => setShowForm(!showForm)}>
            {showForm ? 'Close form' : 'Create organization'}
          </button>
        }
      />
      {error && <Alert>{error}</Alert>}
      {draft && (
        <p className="notice">
          Your proposed organization name is saved for this account in this tab.{' '}
          {identityVerified(account.user)
            ? 'Review your identity status in'
            : 'Verify your identity in'}{' '}
          <a className="text-button" href="#account-security">
            Account security
          </a>
          , then choose Create organization when you are ready.
        </p>
      )}
      {showForm && (
        <form
          className="record-form narrow-form"
          onSubmit={(event) => {
            void create(event);
          }}
        >
          <h2>Create an organization</h2>
          <p>You will become its owner. Your personal workspace and its projects stay separate.</p>
          <label>
            Organization name
            <input
              name="name"
              required
              maxLength={160}
              autoComplete="organization"
              defaultValue={draft}
              autoFocus
            />
          </label>
          <button disabled={busy}>{busy ? 'Creating…' : 'Create organization'}</button>
        </form>
      )}
      <div className="workspace-list">
        {account.workspaces.map((workspace) => (
          <article className="workspace-card" key={workspace.id}>
            <div className="workspace-initial" aria-hidden="true">
              {workspace.name.charAt(0).toUpperCase()}
            </div>
            <div>
              <p className="eyebrow">
                {workspace.kind === 'personal' ? 'Personal workspace' : 'Organization'}
              </p>
              <h2>{workspace.name}</h2>
              <p>{workspace.role} access</p>
            </div>
            <button
              className="secondary"
              disabled={workspace.id === active}
              onClick={() => {
                onSelect(workspace.id);
                location.hash = 'projects';
              }}
            >
              {workspace.id === active ? 'Current workspace' : 'Open workspace'}
            </button>
          </article>
        ))}
      </div>
      <section className="support-note">
        <h2>Organize work by project</h2>
        <p>
          Each workspace contains projects. Scenarios, evaluations, releases, and production
          evidence belong to a project; members and policies are managed within the workspace.
        </p>
        <a className="inline-link" href="#projects">
          Open projects <span aria-hidden="true">→</span>
        </a>
      </section>
    </>
  );
}
