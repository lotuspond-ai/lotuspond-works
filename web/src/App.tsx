import { Suspense, lazy, useCallback, useEffect, useState, useRef, type FormEvent } from 'react';
import { api, ApiError, type Account, type Row } from './api.js';
import { Alert, RelativeTime, StatusBadge } from './components/ui.js';
import { Admin } from './pages/Admin.js';
import { Workspaces } from './pages/Workspaces.js';
import { AccountSecurity } from './pages/AccountSecurity.js';
import { IdentityFlow, IdentityFrame, VerificationCard } from './pages/IdentityFlow.js';
import { Invitations } from './pages/Invitations.js';
import { Landing } from './pages/Landing.js';
import { identityVerified } from './lib/identityStatus.js';
import { isIdentityRoute, type IdentityLinks } from './lib/identityLinks.js';
import {
  organizationDraft,
  registrationPayload,
  saveOrganizationDraft,
} from './lib/registration.js';
import { ScenarioForm } from './components/ScenarioForm.js';
import { RecordDetails } from './components/RecordDetails.js';
import {
  loginChallenge,
  privilegedMfaNeeded,
  type LoginResult,
  type MfaChallenge,
} from './lib/mfa.js';
import { MfaChallengeForm } from './components/MfaChallenge.js';
import { InkGlyph, type GlyphName } from './components/InkGlyph.js';
import { CommandPalette, type Command } from './components/console/CommandPalette.js';
import { Icon } from './components/console/icons.js';
import { allPages, navigation, pageLabel } from './components/console/navigation.js';
import { Sidebar } from './components/console/Sidebar.js';
import { Home } from './pages/Home.js';
import { Planned } from './pages/Planned.js';
import { plannedArea } from './lib/roadmap.js';

// The Studio carries the canvas and the contract validator, so it loads when first opened.
const Studio = lazy(() =>
  import('./pages/Studio.js').then((module) => ({ default: module.Studio })),
);
import { requestCreate, takeCreateRequest, useSetupProgress } from './lib/setup.js';
import { fieldLabel, formatLatency } from './lib/format.js';
import { documentTitle, useDocumentTitle } from './lib/title.js';

type Field = {
  key: string;
  label: string;
  type?: 'text' | 'number' | 'json' | 'checkbox' | 'select' | 'list' | 'email' | 'password';
  options?: string[];
  resource?: string;
  optional?: boolean;
  value?: string;
};
type Definition = { title: string; description: string; fields: Field[] };
const definitions: Record<string, Definition> = {
  projects: {
    title: 'Projects',
    description: 'Define the workflows whose releases you own.',
    fields: [
      { key: 'name', label: 'Project name' },
      { key: 'description', label: 'What does this workflow do?', optional: true },
    ],
  },
  environments: {
    title: 'Environments',
    description:
      'Record where a scenario is evaluated or a deployment runs, and the conditions it requires.',
    fields: [
      { key: 'project_id', label: 'Project', resource: 'projects' },
      { key: 'name', label: 'Environment name' },
      { key: 'kind', label: 'Environment type', options: ['local', 'sandbox', 'production'] },
      { key: 'configuration', label: 'Configuration (JSON)', type: 'json', value: '{}' },
    ],
  },
  scenarios: {
    title: 'Scenarios',
    description:
      'Describe a concrete input, its expected outcome, and whether failure must block release.',
    fields: [
      { key: 'project_id', label: 'Project', resource: 'projects' },
      { key: 'name', label: 'Scenario name' },
      { key: 'input', label: 'Input (JSON)', type: 'json', value: '{}' },
      { key: 'expected', label: 'Expected outcome (JSON)', type: 'json', value: '{}' },
      { key: 'critical', label: 'Critical: a failure blocks the release', type: 'checkbox' },
    ],
  },
  runners: {
    title: 'Runners',
    description:
      'Register an execution worker. Your worker claims evaluations and submits measured results through the API.',
    fields: [
      { key: 'name', label: 'Runner name' },
      {
        key: 'capabilities',
        label: 'Capabilities (comma-separated)',
        type: 'list',
        optional: true,
      },
    ],
  },
  runs: {
    title: 'Evaluation runs',
    description:
      'Freeze a scenario snapshot for a specific revision, then execute it with a registered runner.',
    fields: [
      { key: 'project_id', label: 'Project', resource: 'projects' },
      { key: 'environment_id', label: 'Environment', resource: 'environments', optional: true },
      { key: 'name', label: 'Run name' },
      { key: 'revision', label: 'Code, prompt, or model revision' },
    ],
  },
  policies: {
    title: 'Policies',
    description:
      'Set measurable release limits and tool authorization rules. Policies are immutable; create a new version to change a decision rule.',
    fields: [
      { key: 'name', label: 'Policy name' },
      { key: 'minimum_pass_rate', label: 'Minimum pass rate (0–1)', type: 'number', value: '1' },
      { key: 'maximum_regression', label: 'Maximum regression (0–1)', type: 'number', value: '0' },
      { key: 'maximum_p95_ms', label: 'Maximum P95 latency (ms)', type: 'number', optional: true },
      {
        key: 'maximum_cost_usd',
        label: 'Maximum evaluation cost (USD)',
        type: 'number',
        optional: true,
      },
      {
        key: 'require_approval',
        label: 'Require independent human approval',
        type: 'checkbox',
        value: 'true',
      },
      {
        key: 'blocked_tools',
        label: 'Blocked tools (comma-separated)',
        type: 'list',
        optional: true,
      },
      {
        key: 'approval_tools',
        label: 'Tools requiring approval (comma-separated)',
        type: 'list',
        optional: true,
      },
    ],
  },
  releases: {
    title: 'Releases',
    description:
      'Compare a candidate to its baseline, apply a policy, and retain the evidence behind your decision.',
    fields: [
      { key: 'project_id', label: 'Project', resource: 'projects' },
      { key: 'name', label: 'Release name' },
      { key: 'revision', label: 'Candidate revision' },
      { key: 'candidate_run_id', label: 'Candidate run', resource: 'runs' },
      { key: 'baseline_run_id', label: 'Baseline run', resource: 'runs', optional: true },
      { key: 'policy_id', label: 'Release policy', resource: 'policies' },
    ],
  },
  approvals: {
    title: 'Approvals',
    description:
      'Request independent approval of a release. A different administrator must make the decision.',
    fields: [
      { key: 'release_id', label: 'Release', resource: 'releases' },
      { key: 'action', label: 'Action' },
      { key: 'reason', label: 'Why is approval needed?' },
    ],
  },
  'audit-events': {
    title: 'Audit log',
    description: 'Review the actor and time of each recorded workspace change.',
    fields: [],
  },
  members: {
    title: 'Workspace members',
    description: 'Add an existing Lotuspond account with the least privilege needed.',
    fields: [
      { key: 'email', label: 'Account email', type: 'email' },
      { key: 'role', label: 'Workspace role', options: ['viewer', 'editor', 'admin'] },
    ],
  },
  'api-keys': {
    title: 'API keys',
    description: 'Issue expiring, scoped keys for your own applications and execution workers.',
    fields: [
      { key: 'name', label: 'Key name' },
      {
        key: 'scopes',
        label: 'Scopes: read, write, run, execute, execution_signal',
        type: 'list',
        value: 'read',
      },
      { key: 'days', label: 'Valid for days (1–365)', type: 'number', value: '90' },
    ],
  },
};
const label = (key: string) =>
  pageLabel(key) ||
  definitions[key]?.title ||
  ({ admin: 'Administration' } as Record<string, string>)[key] ||
  'Page not found';
// Pages whose records belong to a project, so the project filter applies to them.
const projectScoped = ['projects', 'studio', 'scenarios', 'environments', 'runs', 'releases'];
// Records a reader can create from the command palette, with the verb the page uses.
const creatable: Record<string, string> = {
  projects: 'Create project',
  studio: 'New workflow',
  scenarios: 'Add scenario',
  runners: 'Register runner',
  runs: 'Start evaluation run',
  releases: 'Create release',
  policies: 'Create policy',
  'api-keys': 'Create API key',
};
// Each record type's empty state carries the field-record glyph for what it will hold.
const emptyGlyph: Record<string, GlyphName> = {
  releases: 'seal',
  policies: 'seal',
  approvals: 'seal',
  scenarios: 'snapshot',
  runs: 'trace',
  'audit-events': 'trace',
  runners: 'network',
  members: 'network',
  'api-keys': 'sealed',
};
const monoFields = ['revision', 'prefix', 'actor_id', 'external_id', 'scopes', 'capabilities'];
// The words a person would use for a row: its name, or what it records.
function recordName(row: Row): string {
  return String(row.name ?? row.title ?? row.action ?? row.external_id ?? row.email ?? row.id);
}
function format(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'object') return JSON.stringify(value);
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  return String(value);
}

export function App({
  identityLinks,
  initialPage,
}: {
  identityLinks: IdentityLinks;
  initialPage: string;
}) {
  const [account, setAccount] = useState<Account | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [workspace, setWorkspace] = useState('');
  const [projects, setProjects] = useState<Row[]>([]);
  const [projectId, setProjectId] = useState('');
  const [projectVersion, setProjectVersion] = useState(0);
  const [projectError, setProjectError] = useState('');
  const [page, setPage] = useState(initialPage);
  const [linkVersion, setLinkVersion] = useState(0);
  const [navOpen, setNavOpen] = useState(false);
  const [searching, setSearching] = useState(false);
  const [announcement, setAnnouncement] = useState('');
  const announcedPage = useRef(page);
  const navOpenNow = useRef(navOpen);
  const focusMainAfterNavigation = useRef(false);
  const setup = useSetupProgress(workspace);
  const refreshSetup = setup.refresh;
  const signedInView =
    !loading && !!account && !isIdentityRoute(page) && page !== 'forgot-password';
  // Signed-out and identity views title themselves.
  useDocumentTitle(
    signedInView
      ? documentTitle(label(page), account.workspaces.find((item) => item.id === workspace)?.name)
      : null,
  );
  // Focus stays on the link that was followed, so the new page is announced politely.
  useEffect(() => {
    if (announcedPage.current === page) return;
    announcedPage.current = page;
    setAnnouncement(label(page));
  }, [page]);
  useEffect(() => {
    navOpenNow.current = navOpen;
  }, [navOpen]);
  useEffect(() => {
    if (!focusMainAfterNavigation.current) return;
    focusMainAfterNavigation.current = false;
    document.getElementById('main-content')?.focus();
  }, [page, linkVersion]);
  useEffect(() => {
    if (page === 'home') refreshSetup();
  }, [page, refreshSetup]);
  useEffect(() => {
    if (!account) return;
    const toggle = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setSearching((open) => !open);
      }
    };
    document.addEventListener('keydown', toggle);
    return () => document.removeEventListener('keydown', toggle);
  }, [account]);
  const refreshAccount = useCallback(async (preferredWorkspace?: string) => {
    try {
      const data = await api<Account>('/me');
      setAccount(data);
      setWorkspace((current) =>
        preferredWorkspace && data.workspaces.some((item) => item.id === preferredWorkspace)
          ? preferredWorkspace
          : data.workspaces.some((item) => item.id === current)
            ? current
            : data.workspaces[0]?.id || '',
      );
      setError('');
    } catch (e) {
      setAccount(null);
      setError(e instanceof ApiError && e.status === 401 ? '' : (e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    void refreshAccount();
    const handler = () => {
      const fromMenu = navOpenNow.current;
      setPage(identityLinks.capture(window.location, window.history));
      setLinkVersion((value) => value + 1);
      setNavOpen(false);
      // The narrow-screen menu hides once a page is chosen, taking the focused link with it,
      // so reading continues at the new page instead of the top of the document.
      if (fromMenu) focusMainAfterNavigation.current = true;
    };
    window.addEventListener('hashchange', handler);
    return () => window.removeEventListener('hashchange', handler);
  }, [refreshAccount, identityLinks]);
  useEffect(() => {
    if (!workspace) return;
    const controller = new AbortController();
    setProjectId('');
    setProjects([]);
    setProjectError('');
    void api<{ items: Row[] }>(
      '/projects?limit=100',
      workspace,
      undefined,
      'GET',
      controller.signal,
    )
      .then((data) => {
        if (!controller.signal.aborted) setProjects(data.items);
      })
      .catch((reason: Error) => {
        if (!controller.signal.aborted) setProjectError(reason.message);
      });
    return () => controller.abort();
  }, [workspace, projectVersion]);
  useEffect(() => {
    if (account && (page === 'login' || page === 'register'))
      location.hash = identityLinks.token('accept-invitation')
        ? 'accept-invitation'
        : organizationDraft(account.user.email)
          ? 'account-security'
          : 'home';
  }, [account, page, identityLinks]);
  if (loading)
    return (
      <div className="center" role="status">
        Opening Lotuspond…
      </div>
    );
  const invitationToken = identityLinks.token('accept-invitation');
  if (
    (isIdentityRoute(page) || page === 'forgot-password') &&
    !(page === 'accept-invitation' && !account && invitationToken)
  ) {
    return (
      <IdentityFrame signedIn={!!account}>
        <IdentityFlow
          key={`${page}-${linkVersion}`}
          page={page}
          links={identityLinks}
          account={account}
          onRefresh={refreshAccount}
          onAccepted={async (id) => {
            await refreshAccount(id);
            location.hash = 'workspaces';
          }}
        />
      </IdentityFrame>
    );
  }
  if (!account)
    return (
      <Welcome
        key={page}
        onReady={async () => {
          await refreshAccount();
        }}
        initialError={error}
        initialMode={
          page === 'register'
            ? 'register'
            : page === 'login' || page === 'accept-invitation'
              ? 'login'
              : 'intro'
        }
        invitationToken={invitationToken}
      />
    );
  const currentWorkspace = account.workspaces.find((item) => item.id === workspace);
  const role = currentWorkspace?.role || 'viewer';
  const hiddenPages =
    currentWorkspace?.kind === 'organization' && ['owner', 'admin'].includes(role)
      ? []
      : ['invitations'];
  const commands: Command[] = [
    ...allPages
      .filter((item) => !hiddenPages.includes(item.id))
      .map((item) => ({
        id: `go-${item.id}`,
        label: item.label,
        section: 'Go to',
        icon: item.icon,
        run: () => {
          location.hash = item.id;
        },
      })),
    ...(role === 'viewer'
      ? []
      : Object.entries(creatable)
          .filter(
            ([id]) => !['policies', 'api-keys'].includes(id) || ['owner', 'admin'].includes(role),
          )
          .map(([id, action]) => ({
            id: `create-${id}`,
            label: action,
            section: 'Create',
            icon: 'plus' as const,
            run: () => requestCreate(id),
          }))),
  ];
  async function signOut() {
    try {
      await api('/auth/logout', undefined, {});
      setAccount(null);
      setWorkspace('');
    } catch (reason) {
      setError((reason as Error).message);
    }
  }
  return (
    <div className="shell">
      <a
        className="skip-link"
        href="#main-content"
        onClick={(event) => {
          event.preventDefault();
          document.getElementById('main-content')?.focus();
        }}
      >
        Skip to content
      </a>
      <Sidebar
        account={account}
        workspace={currentWorkspace}
        onWorkspace={setWorkspace}
        page={page}
        hidden={hiddenPages}
        navOpen={navOpen}
        onToggleNav={() => setNavOpen(!navOpen)}
        onSearch={() => setSearching(true)}
        setup={setup}
        onSignOut={() => void signOut()}
      />
      <div className="main-panel">
        <header className="topbar">
          <nav className="crumbs" aria-label="Breadcrumb">
            <span>{currentWorkspace?.name}</span>
            <span className="crumb-divider" aria-hidden="true">
              /
            </span>
            {projectScoped.includes(page) ? (
              <label className="project-filter">
                <span className="sr-only">Project</span>
                <select value={projectId} onChange={(event) => setProjectId(event.target.value)}>
                  <option value="">All projects</option>
                  {projects.map((project) => (
                    <option key={project.id} value={project.id}>
                      {String(project.name)}
                    </option>
                  ))}
                </select>
              </label>
            ) : (
              <span aria-current="page">{label(page)}</span>
            )}
          </nav>
          <div className="topbar-actions">
            {projectError && (
              <span className="topbar-warning" role="alert">
                Projects unavailable
              </span>
            )}
            <a className="topbar-link" href="#changelog">
              <Icon name="news" />
              <span className="topbar-link-text">What’s new</span>
            </a>
          </div>
        </header>
        <main className="content" id="main-content" tabIndex={-1}>
          {error && <Alert>{error}</Alert>}
          {privilegedMfaNeeded(account) && page !== 'account-security' && (
            <p className="notice">
              {account.mfa?.enabled
                ? 'Verify this session with your authenticator before platform administration.'
                : 'Set up an authenticator before using platform administration.'}{' '}
              <a className="text-button" href="#account-security">
                Open account security
              </a>
            </p>
          )}
          {!identityVerified(account.user) &&
            currentWorkspace?.kind === 'organization' &&
            page !== 'account-security' && (
              <p className="notice">
                Verify your identity to access this organization.{' '}
                <a className="text-button" href="#account-security">
                  Open account security
                </a>
              </p>
            )}
          {invitationToken && (
            <p className="notice">
              You have an invitation waiting in this tab.{' '}
              <a className="text-button" href="#accept-invitation">
                Continue invitation
              </a>
            </p>
          )}
          {page === 'home' ? (
            <Home
              key={workspace}
              account={account}
              workspace={workspace}
              role={role}
              setup={setup}
              onOpenSearch={() => setSearching(true)}
            />
          ) : page === 'invitations' && currentWorkspace ? (
            <Invitations key={workspace} workspace={currentWorkspace} account={account} />
          ) : page === 'account-security' ? (
            <AccountSecurity
              account={account}
              onSignedOut={refreshAccount}
              onRefresh={refreshAccount}
            />
          ) : page === 'workspaces' ? (
            <Workspaces
              account={account}
              active={workspace}
              onSelect={setWorkspace}
              onCreated={refreshAccount}
            />
          ) : page === 'admin' ? (
            account.user.is_platform_admin ? (
              privilegedMfaNeeded(account) ? (
                <section className="notice">
                  <h1>Secure platform access</h1>
                  <p>
                    {account.mfa?.enabled
                      ? 'Sign out and sign back in with an authenticator or recovery code to access platform administration.'
                      : 'Enable an authenticator in Account security to access platform administration.'}
                  </p>
                  <a className="text-button" href="#account-security">
                    Open account security
                  </a>
                </section>
              ) : (
                <Admin />
              )
            ) : (
              <Alert>Platform administrator access is required.</Alert>
            )
          ) : page === 'studio' ? (
            <Suspense
              fallback={
                <p className="loading-state" role="status">
                  Opening the Studio…
                </p>
              }
            >
              <Studio
                key={`${workspace}-${projectId}`}
                workspace={workspace}
                projectId={projectId}
                projectName={String(projects.find((item) => item.id === projectId)?.name ?? '')}
                role={currentWorkspace?.role || 'viewer'}
              />
            </Suspense>
          ) : page === 'settings' ? (
            <Settings
              workspace={workspace}
              role={currentWorkspace?.role || 'viewer'}
              account={account}
              onRefresh={refreshAccount}
            />
          ) : page === 'changelog' ? (
            <Changelog />
          ) : plannedArea(page) ? (
            <Planned area={plannedArea(page)!} />
          ) : definitions[page] ? (
            <Resource
              key={`${page}-${workspace}`}
              page={page}
              workspace={workspace}
              role={currentWorkspace?.role || 'viewer'}
              projectId={projectId}
              onCreated={() => {
                if (page === 'projects') setProjectVersion((value) => value + 1);
                refreshSetup();
              }}
            />
          ) : (
            <div className="page-heading">
              <div>
                <h1>Page not found</h1>
                <p>
                  This page is not available.{' '}
                  <a className="inline-link" href="#home">
                    Go home
                  </a>
                </p>
              </div>
            </div>
          )}
        </main>
      </div>
      <CommandPalette open={searching} commands={commands} onClose={() => setSearching(false)} />
      <p className="sr-only" role="status">
        {announcement}
      </p>
    </div>
  );
}

function Welcome({
  onReady,
  initialError,
  initialMode,
  invitationToken,
}: {
  onReady: (preferredWorkspace?: string) => Promise<void>;
  initialError: string;
  initialMode: 'intro' | 'login' | 'register';
  invitationToken?: string;
}) {
  const [mode, setMode] = useState<'intro' | 'login' | 'register'>(initialMode);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [accountType, setAccountType] = useState<'individual' | 'organization'>('individual');
  const [verificationFirst, setVerificationFirst] = useState(false);
  const [challenge, setChallenge] = useState<MfaChallenge | null>(null);
  const pendingLogin = useRef<AbortController | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const errorMessage = useRef<HTMLParagraphElement>(null);
  useDocumentTitle(
    documentTitle(
      mode === 'register' ? 'Create account' : mode === 'login' ? 'Sign in' : undefined,
    ),
  );
  useEffect(() => {
    if (mode !== 'intro' && !challenge) heading.current?.focus();
  }, [mode, challenge]);
  useEffect(() => () => pendingLogin.current?.abort(), []);
  useEffect(() => {
    if (error) errorMessage.current?.focus();
  }, [error]);
  async function finishSignIn(workspaceId?: string) {
    await onReady(workspaceId);
    setChallenge(null);
    if (invitationToken) location.hash = 'accept-invitation';
    else if (verificationFirst) location.hash = 'account-security';
    else if (['login', 'register'].includes(location.hash.slice(1))) location.hash = 'home';
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError('');
    const form = event.currentTarget;
    const input = Object.fromEntries(new FormData(form));
    delete input.account_choice;
    const controller = new AbortController();
    pendingLogin.current = controller;
    try {
      const result = await api<LoginResult>(
        `/auth/${mode}`,
        undefined,
        mode === 'register' ? registrationPayload(input, accountType, invitationToken) : input,
        'POST',
        controller.signal,
      );
      if (controller.signal.aborted) return;
      const secondStep = loginChallenge(result);
      if (secondStep) {
        form.reset();
        setChallenge(secondStep);
      } else {
        form.reset();
        await finishSignIn('workspaceId' in result ? result.workspaceId : undefined);
      }
    } catch (e) {
      if (controller.signal.aborted) return;
      if (
        e instanceof ApiError &&
        e.code === 'identity_unverified' &&
        mode === 'register' &&
        accountType === 'organization'
      ) {
        saveOrganizationDraft(String(input.email), String(input.workspace || ''));
        setAccountType('individual');
        setVerificationFirst(true);
        setError(
          'Create your personal account first, then verify your email in Account security. You can create the organization from Your workspaces afterward; its name is saved as a draft in this tab. Your entered details are preserved.',
        );
      } else setError((e as Error).message);
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }
  function openAccount(next: 'login' | 'register', choice?: 'individual' | 'organization') {
    if (choice && !verificationFirst) setAccountType(choice);
    setMode(next);
    window.scrollTo(0, 0);
  }
  if (mode === 'intro')
    return (
      <Landing
        error={initialError}
        onCreateAccount={(choice) => openAccount('register', choice)}
        onSignIn={() => openAccount('login')}
      />
    );
  return (
    <div className="welcome">
      <header>
        <a className="wordmark" href="/">
          Lotuspond
          <span className="wordmark-dot" />
        </a>
        <button
          className="text-button"
          disabled={busy || !!challenge}
          onClick={() => setMode(mode === 'login' ? 'register' : 'login')}
        >
          {mode === 'login' ? 'Create account' : 'Sign in'} <span aria-hidden="true">↗</span>
        </button>
      </header>
      {initialError && <Alert>{initialError}</Alert>}
      <main className="welcome-grid auth-active">
        <section>
          <p className="eyebrow">Enterprise agent automation</p>
          {/* A tagline, not a heading: the form's "Welcome back" is this page's one h1. */}
          <p className="hero-title">
            Agents at work,
            <br /> people in charge.
          </p>
          <p className="lede">
            Put AI agents to work in real business processes, with rules where rules suffice and
            people where accountability belongs. Agent testing, release decisions and a Studio for
            workflow packages are available today.
          </p>
          <button
            className="text-button"
            disabled={busy || !!challenge}
            onClick={() => {
              setError('');
              setMode('intro');
            }}
          >
            <span aria-hidden="true">←</span> Back to overview
          </button>
        </section>
        <section className="auth-panel">
          {challenge ? (
            <MfaChallengeForm
              challenge={challenge}
              onComplete={finishSignIn}
              onCancel={(message) => {
                setChallenge(null);
                setError(message || '');
                heading.current?.focus();
              }}
            />
          ) : (
            <>
              <p className="eyebrow">Your workspace</p>
              <h1 className="auth-title" ref={heading} tabIndex={-1}>
                {mode === 'register' ? 'Start with Lotuspond' : 'Welcome back'}
              </h1>
              {invitationToken && (
                <p className="notice">
                  Sign in or create an account with the email that received your invitation. After
                  verifying your email, you can accept it.
                </p>
              )}
              <form
                onSubmit={(event) => {
                  void submit(event);
                }}
              >
                {mode === 'register' && (
                  <>
                    <label>
                      Your name
                      <input name="name" required maxLength={160} autoComplete="name" />
                    </label>
                    <fieldset className="account-type">
                      <legend>How will you use Lotuspond?</legend>
                      <label>
                        <input
                          type="radio"
                          name="account_choice"
                          value="individual"
                          checked={accountType === 'individual'}
                          onChange={() => setAccountType('individual')}
                        />
                        <span>
                          <strong>Individual developer</strong>
                          <small>A personal workspace for your projects</small>
                        </span>
                      </label>
                      <label>
                        <input
                          type="radio"
                          name="account_choice"
                          value="organization"
                          checked={accountType === 'organization'}
                          disabled={verificationFirst}
                          onChange={() => setAccountType('organization')}
                        />
                        <span>
                          <strong>Organization</strong>
                          <small>A shared workspace for your team</small>
                        </span>
                      </label>
                    </fieldset>
                    {accountType === 'organization' && (
                      <label>
                        Organization name
                        <input
                          name="workspace"
                          required
                          maxLength={160}
                          autoComplete="organization"
                        />
                      </label>
                    )}
                  </>
                )}
                <label>
                  Email
                  <input name="email" type="email" required autoComplete="email" />
                </label>
                <label>
                  Password
                  <input
                    name="password"
                    type="password"
                    minLength={mode === 'register' ? 12 : 1}
                    maxLength={256}
                    required
                    autoComplete={mode === 'register' ? 'new-password' : 'current-password'}
                    aria-describedby={mode === 'register' ? 'register-password-hint' : undefined}
                  />
                </label>
                {mode === 'register' && (
                  <p className="hint" id="register-password-hint">
                    Use at least 12 characters.
                  </p>
                )}
                {error && (
                  <p className="error" role="alert" ref={errorMessage} tabIndex={-1}>
                    {error}
                  </p>
                )}
                <button disabled={busy}>
                  {busy ? 'Please wait…' : mode === 'register' ? 'Create account' : 'Sign in'}
                </button>
                {mode === 'login' && (
                  <a className="text-button" href="#forgot-password">
                    Forgot password?
                  </a>
                )}
              </form>
            </>
          )}
        </section>
      </main>
      <footer>
        <span>Lotuspond</span>
        <span>Release decisions grounded in evidence.</span>
      </footer>
    </div>
  );
}

function Resource({
  page,
  workspace,
  role,
  projectId,
  onCreated,
}: {
  page: string;
  workspace: string;
  role: string;
  projectId: string;
  onCreated: () => void;
}) {
  const def = definitions[page];
  const group = navigation.find((section) => section.items.some((item) => item.id === page))?.title;
  const [rows, setRows] = useState<Row[]>([]);
  const [state, setState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [error, setError] = useState('');
  const [showForm, setShowForm] = useState(() => takeCreateRequest(page));
  const [detail, setDetail] = useState<unknown>(null);
  const detailRef = useRef<HTMLElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  const open = detail !== null;
  useEffect(() => {
    if (detail !== null) detailRef.current?.focus();
  }, [detail]);
  // The details drawer is modal: Escape closes it, the page behind stops scrolling, and focus
  // returns to the control that opened it.
  useEffect(() => {
    if (!open) return;
    const close = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setDetail(null);
    };
    document.addEventListener('keydown', close);
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', close);
      document.body.style.overflow = '';
      // An action can remove its own button (an approved request loses Approve), so fall back
      // to the page rather than dropping focus on the document.
      if (opener.current?.isConnected) opener.current.focus();
      else document.getElementById('main-content')?.focus();
    };
  }, [open]);
  function trapFocus(event: React.KeyboardEvent<HTMLElement>) {
    if (event.key !== 'Tab' || !detailRef.current) return;
    const focusable = [
      ...detailRef.current.querySelectorAll<HTMLElement>(
        'button:not(:disabled), a[href], input, select, textarea, summary, [tabindex="0"]',
      ),
    ];
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (!first || !last) return;
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }
  const [secret, setSecret] = useState('');
  const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState('');
  const [next, setNext] = useState<{ before: string; before_id: string } | null>(null);
  // `quiet` refreshes in place, keeping the rows (and a focused row button) on the page.
  const load = useCallback(
    async (append = false, cursor?: { before: string; before_id: string }, quiet = false) => {
      setError('');
      if (!append && !quiet) setState('loading');
      try {
        const data = await api<{
          items: Row[];
          next?: { before: string; before_id: string } | null;
        }>(
          `/${page}?${new URLSearchParams({ ...(cursor || {}), ...(projectId && ['scenarios', 'environments', 'runs', 'releases'].includes(page) ? { project_id: projectId } : {}) })}`,
          workspace,
        );
        setRows((current) => (append ? [...current, ...data.items] : data.items));
        setNext(data.next || null);
        setState('ready');
      } catch (e) {
        setError((e as Error).message);
        setState('error');
      }
    },
    [page, workspace, projectId],
  );
  useEffect(() => {
    void load();
  }, [load]);
  // `from` is the control that asked, so closing the drawer that shows the answer returns there.
  async function action(path: string, body: unknown, from: HTMLElement) {
    opener.current = from;
    setBusy(true);
    setError('');
    try {
      const value = await api<unknown>(path, workspace, body);
      setDetail(value);
      if (body !== undefined) await load(false, undefined, true);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function download(id: string) {
    setBusy(true);
    setError('');
    try {
      const evidence = await api(`/releases/${id}/evidence`, workspace);
      const url = URL.createObjectURL(
        new Blob([JSON.stringify(evidence, null, 2)], { type: 'application/json' }),
      );
      const a = document.createElement('a');
      a.href = url;
      a.download = `lotuspond-release-${id}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const canCreate =
    def.fields.length > 0 &&
    role !== 'viewer' &&
    (!['policies', 'members', 'api-keys'].includes(page) || ['owner', 'admin'].includes(role));
  const noun =
    (
      {
        projects: 'project',
        environments: 'environment',
        scenarios: 'scenario',
        runners: 'runner',
        runs: 'evaluation run',
        policies: 'policy',
        releases: 'release',
        approvals: 'approval request',
        members: 'member',
        'api-keys': 'API key',
      } as Record<string, string>
    )[page] ?? 'record';
  const filtered = rows.filter(
    (row) =>
      (!projectId ||
        (page === 'projects'
          ? row.id === projectId
          : !['scenarios', 'environments', 'runs', 'releases'].includes(page) ||
            row.project_id === projectId)) &&
      JSON.stringify(row).toLowerCase().includes(filter.toLowerCase()),
  );
  const fields =
    page === 'audit-events'
      ? ['action', 'actor_id', 'created_at']
      : page === 'approvals'
        ? ['action', 'status', 'reason', 'created_at']
        : page === 'members'
          ? ['name', 'email', 'role']
          : page === 'api-keys'
            ? ['name', 'prefix', 'scopes', 'expires_at', 'revoked_at']
            : [
                'name',
                ...(page === 'runs'
                  ? ['revision', 'status']
                  : page === 'releases'
                    ? ['revision']
                    : page === 'runners'
                      ? ['capabilities', 'last_seen_at']
                      : []),
                'created_at',
              ];
  return (
    <>
      <div className="page-heading">
        <div>
          {group && group !== def.title && <p className="eyebrow">{group}</p>}
          <h1>{def.title}</h1>
          <p>{def.description}</p>
        </div>
        {canCreate && (
          <button onClick={() => setShowForm(!showForm)}>
            {showForm ? 'Close form' : `Add ${noun}`}
          </button>
        )}
      </div>
      {showForm &&
        (page === 'scenarios' ? (
          <ScenarioForm
            workspace={workspace}
            projectId={projectId}
            onSubmit={async (data) => {
              await api('/scenarios', workspace, data);
              setShowForm(false);
              await load();
              onCreated();
            }}
          />
        ) : (
          <RecordForm
            definition={def}
            workspace={workspace}
            projectId={projectId}
            onSubmit={async (data) => {
              const result = await api<{ key?: string }>(`/${page}`, workspace, data);
              if (result.key) setSecret(result.key);
              setShowForm(false);
              await load();
              onCreated();
            }}
          />
        ))}
      {secret && (
        <div className="notice">
          <strong>Save your API key now</strong>
          <code>{secret}</code>
          <p>This key will not be displayed again.</p>
          <button className="secondary" onClick={() => setSecret('')}>
            I have saved it
          </button>
        </div>
      )}
      <div className="toolbar">
        <label className="search">
          <span className="sr-only">Filter records</span>
          <input
            placeholder="Filter loaded records…"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
          />
        </label>
        {/* Always present, so a filter's new count is announced as it changes. */}
        <span className="toolbar-count" role="status">
          {state !== 'ready'
            ? ''
            : filtered.length === rows.length
              ? `${rows.length} ${rows.length === 1 ? 'record' : 'records'}`
              : `${filtered.length} of ${rows.length} records`}
        </span>
        <button
          className="secondary"
          onClick={() => {
            void load();
          }}
        >
          Refresh
        </button>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}{' '}
          <button
            className="text-button"
            onClick={() => {
              void load();
            }}
          >
            Retry
          </button>
        </p>
      )}
      {state === 'loading' ? (
        <div className="table-wrap" role="status">
          <span className="sr-only">Loading {def.title.toLowerCase()}…</span>
          <table aria-hidden="true">
            <tbody>
              {[0, 1, 2].map((line) => (
                <tr key={line} className="skeleton-row">
                  {fields.map((field, column) => (
                    <td key={field}>
                      <span style={{ width: `${[64, 36, 48, 28][(column + line) % 4]}%` }} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : state === 'error' ? null : filtered.length === 0 ? (
        <div className="empty">
          {!filter && <InkGlyph name={emptyGlyph[page] ?? 'enclosure'} />}
          <h2>{filter ? 'No matching records' : `No ${def.title.toLowerCase()} yet`}</h2>
          <p>
            {filter
              ? 'Try another filter, or clear it to see every loaded record.'
              : def.description}
          </p>
          {canCreate && !filter && <button onClick={() => setShowForm(true)}>Add {noun}</button>}
          {filter && (
            <button className="secondary" onClick={() => setFilter('')}>
              Clear filter
            </button>
          )}
        </div>
      ) : (
        <div className="table-wrap">
          <table>
            <caption className="sr-only">{def.title}</caption>
            <thead>
              <tr>
                {fields.map((field) => (
                  <th key={field} scope="col">
                    {fieldLabel(field)}
                  </th>
                ))}
                <th className="actions">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((row) => (
                <tr key={row.id}>
                  {fields.map((field) => (
                    <td
                      key={field}
                      className={
                        field === fields[0]
                          ? 'primary-cell'
                          : monoFields.includes(field)
                            ? 'cell-mono'
                            : undefined
                      }
                    >
                      {field === 'status' ? (
                        <StatusBadge value={row[field]} />
                      ) : field.endsWith('_at') && row[field] ? (
                        <RelativeTime value={row[field]} />
                      ) : field === 'latency_ms' && Number.isFinite(Number(row[field])) ? (
                        formatLatency(Number(row[field]))
                      ) : (
                        format(row[field])
                      )}
                    </td>
                  ))}
                  <td className="actions">
                    {/* Each action names its record, so a list of "Open" buttons stays distinct. */}
                    <button
                      className="text-button"
                      aria-label={`Open ${recordName(row)}`}
                      onClick={(event) => {
                        opener.current = event.currentTarget;
                        if (page === 'runs')
                          void action(
                            `/${page}/${encodeURIComponent(row.id)}`,
                            undefined,
                            event.currentTarget,
                          );
                        else setDetail(row);
                      }}
                    >
                      Open
                    </button>
                    {page === 'releases' && (
                      <>
                        <button
                          className="text-button"
                          aria-label={`Evaluate ${recordName(row)}`}
                          disabled={busy || role === 'viewer'}
                          onClick={(event) => {
                            void action(`/releases/${row.id}/evaluate`, {}, event.currentTarget);
                          }}
                        >
                          Evaluate
                        </button>
                        <button
                          className="text-button"
                          aria-label={`Export ${recordName(row)}`}
                          disabled={busy}
                          onClick={() => {
                            void download(row.id);
                          }}
                        >
                          Export
                        </button>
                      </>
                    )}
                    {page === 'runs' &&
                      ['queued', 'running'].includes(String(row.status)) &&
                      role !== 'viewer' && (
                        <button
                          className="text-button"
                          aria-label={`Cancel ${recordName(row)}`}
                          disabled={busy}
                          onClick={(event) => {
                            void action(`/runs/${row.id}/cancel`, {}, event.currentTarget);
                          }}
                        >
                          Cancel
                        </button>
                      )}
                    {page === 'approvals' &&
                      row.status === 'pending' &&
                      ['owner', 'admin'].includes(role) && (
                        <>
                          <button
                            className="text-button"
                            aria-label={`Approve ${recordName(row)}`}
                            disabled={busy}
                            onClick={(event) => {
                              void action(
                                `/approvals/${row.id}/decide`,
                                { status: 'approved' },
                                event.currentTarget,
                              );
                            }}
                          >
                            Approve
                          </button>
                          <button
                            className="text-button danger"
                            aria-label={`Reject ${recordName(row)}`}
                            disabled={busy}
                            onClick={(event) => {
                              void action(
                                `/approvals/${row.id}/decide`,
                                { status: 'rejected' },
                                event.currentTarget,
                              );
                            }}
                          >
                            Reject
                          </button>
                        </>
                      )}
                    {page === 'api-keys' && !row.revoked_at && (
                      <button
                        className="text-button danger"
                        aria-label={`Revoke ${recordName(row)}`}
                        disabled={busy}
                        onClick={(event) => {
                          const from = event.currentTarget;
                          if (
                            window.confirm(
                              'Revoke this key? Applications using it will lose access.',
                            )
                          )
                            void action(`/api-keys/${row.id}/revoke`, {}, from);
                        }}
                      >
                        Revoke
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {state === 'ready' && next && <p className="footnote">Older records are available below.</p>}
      {next && (
        <button
          className="secondary load-more"
          onClick={() => {
            void load(true, next);
          }}
        >
          Load older records
        </button>
      )}
      {detail !== null && <div className="drawer-backdrop" onClick={() => setDetail(null)} />}
      {detail !== null && (
        <section
          className="detail"
          ref={detailRef}
          tabIndex={-1}
          role="dialog"
          aria-modal="true"
          aria-label={`${def.title}: record`}
          onKeyDown={trapFocus}
        >
          <div>
            <h2>Record</h2>
            <button className="secondary" onClick={() => setDetail(null)}>
              Close
            </button>
          </div>
          <RecordDetails value={detail} />
        </section>
      )}
    </>
  );
}

function RecordForm({
  definition,
  workspace,
  projectId,
  onSubmit,
}: {
  definition: Definition;
  workspace: string;
  projectId: string;
  onSubmit: (data: Record<string, unknown>) => Promise<void>;
}) {
  const [choices, setChoices] = useState<Record<string, Row[]>>({});
  const [selectedProject, setSelectedProject] = useState(projectId);
  const [choicesLoading, setChoicesLoading] = useState(true);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const entries = await Promise.all(
          [
            ...new Set(
              definition.fields.flatMap((field) => (field.resource ? [field.resource] : [])),
            ),
          ].map(
            async (resource) =>
              [
                resource,
                (await api<{ items: Row[] }>(`/${resource}?limit=100`, workspace)).items,
              ] as const,
          ),
        );
        if (alive) {
          setChoices(Object.fromEntries(entries));
          setChoicesLoading(false);
        }
      } catch (e) {
        if (alive) {
          setError((e as Error).message);
          setChoicesLoading(false);
        }
      }
    })();
    return () => {
      alive = false;
    };
  }, [definition, workspace]);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      const form = new FormData(event.currentTarget);
      const data: Record<string, unknown> = {};
      for (const field of definition.fields) {
        const raw = String(form.get(field.key) || '');
        data[field.key] =
          field.type === 'checkbox'
            ? form.has(field.key)
            : field.type === 'json'
              ? JSON.parse(raw)
              : field.type === 'list'
                ? raw
                    .split(',')
                    .map((value) => value.trim())
                    .filter(Boolean)
                : field.type === 'number'
                  ? raw === ''
                    ? null
                    : Number(raw)
                  : raw === '' && field.optional && field.resource
                    ? null
                    : raw;
      }
      if (definition.title === 'Approvals' && data.release_id) data.action = 'release';
      await onSubmit(data);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <form
      className="record-form"
      onSubmit={(event) => {
        void submit(event);
      }}
    >
      <div className="form-grid">
        {definition.fields.map((field) => (
          <label
            key={field.key}
            className={
              field.type === 'checkbox' ? 'checkbox-label' : field.type === 'json' ? 'wide' : ''
            }
          >
            {field.type === 'checkbox' ? (
              <>
                <input type="checkbox" name={field.key} defaultChecked={field.value === 'true'} />
                {field.label}
              </>
            ) : (
              <>
                {field.label}
                {field.optional && <span className="optional"> Optional</span>}
                {field.resource || field.options ? (
                  <select
                    key={`${field.key}-${field.resource !== 'projects' ? selectedProject : ''}`}
                    name={field.key}
                    required={!field.optional}
                    value={field.key === 'project_id' ? selectedProject : undefined}
                    defaultValue={field.key === 'project_id' ? undefined : ''}
                    onChange={
                      field.key === 'project_id'
                        ? (event) => setSelectedProject(event.target.value)
                        : undefined
                    }
                  >
                    <option value="">{field.optional ? 'None' : 'Choose…'}</option>
                    {field.options?.map((value) => (
                      <option key={value}>{value}</option>
                    ))}
                    {field.resource &&
                      choices[field.resource]
                        ?.filter(
                          (row) =>
                            !selectedProject ||
                            !['runs', 'environments', 'scenarios'].includes(field.resource || '') ||
                            row.project_id === selectedProject,
                        )
                        .map((row) => (
                          <option key={row.id} value={row.id}>
                            {String(row.name || row.title || row.external_id || row.id)}
                            {row.revision ? ` · ${row.revision}` : ''}
                          </option>
                        ))}
                  </select>
                ) : field.key === 'action' ? (
                  <>
                    <input name="action" value="release" readOnly />
                    <span className="hint">
                      This approval authorizes the selected release. A different administrator must
                      review it.
                    </span>
                  </>
                ) : field.type === 'json' ? (
                  <textarea
                    name={field.key}
                    required={!field.optional}
                    rows={4}
                    defaultValue={field.value}
                  />
                ) : (
                  <input
                    name={field.key}
                    type={field.type === 'list' ? 'text' : field.type || 'text'}
                    step={field.type === 'number' ? 'any' : undefined}
                    required={!field.optional}
                    defaultValue={field.value}
                  />
                )}
              </>
            )}
          </label>
        ))}
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <button disabled={busy || choicesLoading}>
        {busy ? 'Saving…' : choicesLoading ? 'Loading choices…' : 'Save record'}
      </button>
    </form>
  );
}

function Settings({
  workspace,
  role,
  account,
  onRefresh,
}: {
  workspace: string;
  role: string;
  account: Account;
  onRefresh: () => Promise<void>;
}) {
  const [data, setData] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);
  const canEdit = ['owner', 'admin'].includes(role);
  useEffect(() => {
    setData(null);
    setError('');
    void api<Record<string, unknown>>('/settings', workspace)
      .then(setData)
      .catch((e) => setError(e.message));
  }, [workspace]);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setError('');
    setSaved(false);
    setSaving(true);
    try {
      await api('/settings', workspace, { name: String(form.get('name')) }, 'PATCH');
      setSaved(true);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }
  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">Administer</p>
          <h1>Settings</h1>
          <p>Manage your workspace name and access policy.</p>
        </div>
      </div>
      <VerificationCard account={account} onRefresh={onRefresh} />
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {data ? (
        <>
          <form
            className="record-form"
            onSubmit={(event) => {
              void submit(event);
            }}
          >
            <label>
              Workspace name
              <input name="name" required disabled={!canEdit} defaultValue={String(data.name)} />
            </label>
            {canEdit ? (
              <button disabled={saving}>{saving ? 'Saving…' : 'Save settings'}</button>
            ) : (
              <p className="hint">A workspace owner or administrator can change these settings.</p>
            )}
            {saved && <p role="status">Settings saved.</p>}
          </form>
        </>
      ) : !error ? (
        <p role="status">Loading settings…</p>
      ) : null}
    </>
  );
}
function Changelog() {
  const [items, setItems] = useState<
    { id: string; date: string; title: string; description: string }[]
  >([]);
  const [error, setError] = useState('');
  useEffect(() => {
    void api<{ items: typeof items }>('/changelog')
      .then((data) => setItems(data.items))
      .catch((e) => setError(e.message));
  }, []);
  return (
    <>
      <div className="page-heading">
        <div>
          <p className="eyebrow">Lotuspond</p>
          <h1>What’s new</h1>
        </div>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {items.map((item) => (
        <article className="change" key={item.id}>
          <time>{item.date}</time>
          <h2>{item.title}</h2>
          <p>{item.description}</p>
        </article>
      ))}
    </>
  );
}
