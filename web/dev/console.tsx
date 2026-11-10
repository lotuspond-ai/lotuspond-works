// Development-only visual harness for the signed-in console. Vite serves it at
// /dev/console.html during `npm run dev:web`; `vite build` only builds web/index.html, so it
// never ships. It answers the console's API requests from the fixtures below, so layouts can
// be reviewed without an account or a database. Append `?empty` to see every list empty,
// `?failing` to see every list fail, or `?viewer` to see every workspace as a read-only
// member, whose writes the fixtures refuse as the API does. Fixture records are fictional and
// never leave this page.
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from '../src/App.js';
import { createIdentityLinks } from '../src/lib/identityLinks.js';
import { packageDigest, validatePackage } from '../src/lib/packageContract.js';
import { sampleDecision } from '../src/components/platform/sample.js';
import examplePackage from '../../contracts/examples/reviewed-action.v1.json';
import '../src/styles.css';

const mode = new URLSearchParams(location.search);
const ago = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();
const exampleValidation = validatePackage(examplePackage);
const exampleCanonical = exampleValidation.ok ? exampleValidation.canonical : '';
const VALIDATOR_REVISION = 'lotuspond.workflow-package.validator.v1.1';
const CANONICALIZATION = 'lotuspond.workflow-package.canonical-json.v1';
// Canonical content by fixture version id, so an opened version round-trips through the Studio.
const packageContents = new Map<string, string>([['pkg-1', exampleCanonical]]);
const project = {
  id: 'p-refunds',
  name: 'Refund assistant',
  description: 'Handles refund requests',
};

const lists: Record<string, Record<string, unknown>[]> = {
  projects: [
    { ...project, created_at: ago(9000) },
    { id: 'p-voice', name: 'Voice concierge', description: 'Inbound calls', created_at: ago(7000) },
  ],
  environments: [
    {
      id: 'e1',
      project_id: project.id,
      name: 'Staging sandbox',
      kind: 'sandbox',
      created_at: ago(8000),
    },
  ],
  scenarios: [
    ['Refund within policy', true],
    ['Refund above the approval limit', true],
    ['Order lookup by email', false],
    ['Tool call: issue store credit', true],
  ].map(([name, critical], index) => ({
    id: `s${index}`,
    project_id: project.id,
    name,
    critical,
    modality: 'text',
    created_at: ago(6000 - index * 30),
  })),
  runners: [
    {
      id: 'r1',
      name: 'runner-1',
      capabilities: ['http', 'mcp'],
      last_seen_at: ago(2),
      created_at: ago(5000),
    },
  ],
  runs: [
    {
      id: 'run-42',
      project_id: project.id,
      name: 'Candidate r42',
      revision: 'r42',
      status: 'completed',
      created_at: ago(95),
    },
    {
      id: 'run-43',
      project_id: project.id,
      name: 'Candidate r43',
      revision: 'r43',
      status: 'running',
      created_at: ago(8),
    },
    {
      id: 'run-41',
      project_id: project.id,
      name: 'Baseline r41',
      revision: 'r41',
      status: 'completed',
      created_at: ago(2900),
    },
  ],
  releases: [
    {
      id: 'rel-42',
      project_id: project.id,
      name: 'Refunds r42',
      revision: 'r42',
      created_at: ago(90),
    },
    {
      id: 'rel-41',
      project_id: project.id,
      name: 'Refunds r41',
      revision: 'r41',
      created_at: ago(2880),
    },
  ],
  policies: [
    {
      id: 'pol-1',
      name: 'Refunds release policy v3',
      minimum_pass_rate: 0.95,
      maximum_regression: 0,
      require_approval: true,
      created_at: ago(9100),
    },
  ],
  approvals: [
    {
      id: 'ap-1',
      action: 'release',
      status: 'pending',
      reason: 'Refunds r41 passed its checks',
      created_at: ago(2870),
    },
  ],
  'audit-events': [
    { id: 'a1', action: 'release.evaluated', actor_id: 'you', created_at: ago(90) },
    { id: 'a2', action: 'policy.created', actor_id: 'you', created_at: ago(9100) },
  ],
  members: [
    { id: 'm1', name: 'You', email: 'you@example.test', role: 'owner' },
    { id: 'm2', name: 'Second administrator', email: 'admin@example.test', role: 'admin' },
  ],
  'api-keys': [
    {
      id: 'k1',
      name: 'CI release gate',
      prefix: 'lp_4f2a',
      scopes: ['read', 'run'],
      expires_at: ago(-86_400),
      revoked_at: null,
    },
  ],
  // The registry's reviewed-action example, as the registry would record it. Its digest is the
  // one pinned by packageContract.test.ts and the Python contract tests.
  packages: [
    {
      id: 'pkg-1',
      created_at: ago(2400),
      workspace_id: 'w-org',
      project_id: project.id,
      package_id: examplePackage.package_id,
      version: examplePackage.version,
      schema_version: examplePackage.schema_version,
      validator_revision: VALIDATOR_REVISION,
      canonicalization: CANONICALIZATION,
      sha256: '305efc96011ba5fc2f527cefbe416890b0265269e18e3402354ffbfed40251d3',
      content_bytes: new TextEncoder().encode(exampleCanonical).length,
      publisher_id: 'u1',
      publisher_kind: 'user',
    },
  ],
  'auth/sessions': [
    {
      id: 'sess1',
      current: true,
      created_at: ago(40),
      last_seen_at: ago(1),
      user_agent: 'This browser',
    },
  ],
};

const role = mode.has('viewer') ? 'viewer' : 'owner';
const account = {
  user: {
    id: 'u1',
    email: 'you@example.test',
    name: 'You',
    is_platform_admin: false,
    email_verified: true,
    identity_verified: true,
  },
  workspaces: [
    { id: 'w-personal', name: 'Personal', kind: 'personal', role },
    {
      id: 'w-org',
      name: 'Example organization',
      kind: 'organization',
      role,
      require_mfa: false,
    },
  ],
  mfa: {
    available: true,
    enabled: true,
    session_verified: true,
    recovery_codes_remaining: 8,
    required_for_privileged: false,
  },
  capabilities: { mail_enabled: false, registration_enabled: true },
};

const settings = {
  name: 'Example organization',
  authenticator_policy: {
    require_mfa: false,
    available: true,
    manageable: true,
    members_without_authenticator: 1,
  },
};

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
const passthrough = window.fetch.bind(window);

window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
  const url = new URL(
    typeof input === 'string' || input instanceof URL ? input : input.url,
    location.origin,
  );
  if (!url.pathname.startsWith('/api/v1/')) return passthrough(input, init);
  const path = url.pathname.slice('/api/v1/'.length);
  const method = (init?.method || 'GET').toUpperCase();
  await new Promise((resolve) => setTimeout(resolve, 180));
  if (path === 'changelog') return passthrough(input, init);
  if (path === 'me') return json(account);
  if (path === 'settings') return json(settings);
  // Evaluating a fixture release answers with the landing preview's sample decision, which the
  // tested policy port computed, so the decision layout can be reviewed.
  if (method === 'POST' && /^releases\/[^/]+\/evaluate$/.test(path))
    return json({
      id: 'decision-fixture',
      verdict: sampleDecision.verdict,
      reasons: sampleDecision.reasons,
      created_at: new Date().toISOString(),
      evidence: {
        metrics: {
          passRate: sampleDecision.passRate,
          received: sampleDecision.received,
          total: sampleDecision.total,
          p95Ms: sampleDecision.p95Ms,
          costUsd: null,
        },
      },
    });
  // Publishing a package answers as the registry does: the same local validator decides
  // admission, an identical version is returned again, and a changed one conflicts. The
  // fixture version lives in this page only.
  const publish = /^projects\/([^/]+)\/packages$/.exec(path);
  // A viewer is refused with the API's own message (backend/lotuspond/security.py).
  if (method === 'POST' && publish && role === 'viewer')
    return json({ error: 'forbidden', message: 'Your role has read-only access.' }, 403);
  if (method === 'POST' && publish) {
    let raw: unknown;
    try {
      raw = JSON.parse(typeof init?.body === 'string' ? init.body : '');
    } catch {
      return json({ error: 'invalid_package', message: 'The body is not valid JSON.' }, 422);
    }
    const result = validatePackage(raw);
    if (!result.ok)
      return json(
        {
          error: 'invalid_package',
          message: 'The package does not satisfy the supported contract.',
          errors: result.problems.map(({ path: at, code, message }) => ({
            path: at,
            code,
            message,
          })),
        },
        422,
      );
    const sha256 = await packageDigest(result.canonical);
    const existing = lists.packages.find(
      (item) =>
        item.project_id === publish[1] &&
        item.package_id === result.package.package_id &&
        item.version === result.package.version,
    );
    if (existing) {
      if (existing.sha256 === sha256) return json(existing);
      return json(
        {
          error: 'package_conflict',
          message: 'The immutable version or request key already identifies different content.',
        },
        409,
      );
    }
    const created = {
      id: `pkg-${lists.packages.length + 1}`,
      created_at: new Date().toISOString(),
      workspace_id: 'w-org',
      project_id: publish[1],
      package_id: result.package.package_id,
      version: result.package.version,
      schema_version: result.package.schema_version,
      validator_revision: VALIDATOR_REVISION,
      canonicalization: CANONICALIZATION,
      sha256,
      content_bytes: new TextEncoder().encode(result.canonical).length,
      publisher_id: 'u1',
      publisher_kind: 'user',
    };
    lists.packages.unshift(created);
    packageContents.set(created.id, result.canonical);
    return json(created, 201);
  }
  if (method !== 'GET')
    return json({ error: 'fixture', message: 'The console fixtures are read-only.' }, 400);
  if (mode.has('failing'))
    return json({ error: 'unavailable', message: 'The data service is unavailable.' }, 503);
  const list = lists[path];
  const projectFilter = url.searchParams.get('project_id');
  if (list)
    return json({
      items: mode.has('empty')
        ? []
        : projectFilter
          ? list.filter((item) => !('project_id' in item) || item.project_id === projectFilter)
          : list,
      next: null,
    });
  const content = /^packages\/([^/]+)\/content$/.exec(path);
  if (content) {
    const stored = packageContents.get(content[1]);
    return stored
      ? new Response(stored, { headers: { 'content-type': 'application/json' } })
      : json({ error: 'not_found', message: 'No fixture for this request.' }, 404);
  }
  const [resource, id] = path.split('/');
  const record = resource ? lists[resource]?.find((item) => item.id === id) : undefined;
  if (record) return json(record);
  return json({ error: 'not_found', message: 'No fixture for this request.' }, 404);
};

let storage: Storage | undefined;
try {
  storage = window.sessionStorage;
} catch {
  /* Private browsers can disable storage. */
}
const identityLinks = createIdentityLinks(storage);
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App
      identityLinks={identityLinks}
      initialPage={identityLinks.capture(window.location, window.history)}
    />
  </StrictMode>,
);
