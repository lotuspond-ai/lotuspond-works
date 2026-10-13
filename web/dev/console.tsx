// Development-only visual harness for the signed-in console. Vite serves it at
// /dev/console.html during `npm run dev:web`; `vite build` only builds web/index.html, so it
// never ships. It answers the console's API requests from the fixtures below, so layouts can
// be reviewed without an account or a database. Append `?empty` to see every list empty,
// or `?failing` to see every list fail. Fixture records are fictional and never leave this page.
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from '../src/App.js';
import { createIdentityLinks } from '../src/lib/identityLinks.js';
import { sampleDecision } from '../src/components/platform/sample.js';
import '../src/styles.css';

const mode = new URLSearchParams(location.search);
const ago = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();
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
  integrations: [
    { id: 'i1', name: 'Support gateway', kind: 'mcp', status: 'unverified', created_at: ago(4000) },
  ],
  traces: [
    ['refund.create', 'ok', 1180, null],
    ['order.cancel', 'error', 2840, 0.0019],
    ['refund.lookup', 'ok', 412, 0.0011],
  ].map(([name, status, latency, cost], index) => ({
    id: `t${index}`,
    project_id: project.id,
    name,
    status,
    latency_ms: latency,
    cost_usd: cost,
    cost_measurement: cost === null ? 'unavailable' : 'reported',
    occurred_at: ago(3 + index * 4),
  })),
  receipts: [],
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
  monitors: [
    {
      id: 'mon1',
      project_id: project.id,
      name: 'Refunds cite the policy',
      enabled: true,
      sample_rate: 0.1,
      criteria: 'Cites the refund policy',
      created_at: ago(3000),
    },
  ],
  incidents: [
    {
      id: 'inc1',
      title: 'Assessment unavailable for refund.create',
      severity: 'medium',
      status: 'open',
      category: 'assessment_unavailable',
      source: 'trace_assessment',
      project_id: project.id,
      trace_id: 't0',
      created_at: ago(3),
    },
  ],
  'voice/sessions': [],
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
    { id: 'w-personal', name: 'Personal', kind: 'personal', role: 'owner' },
    {
      id: 'w-org',
      name: 'Example organization',
      kind: 'organization',
      role: 'owner',
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
  monthly_budget_usd: null,
  authenticator_policy: {
    require_mfa: false,
    available: true,
    manageable: true,
    members_without_authenticator: 1,
  },
  billing: {
    plan: 'team',
    entitlements: { monthly_trace_quota: 100_000, trace_retention_days: 90 },
    usage: { traces_this_month: 0, traces_remaining: 100_000 },
    payment: { status: 'not_collected', message: 'Lotuspond does not collect payment.' },
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
  if (method !== 'GET')
    return json({ error: 'fixture', message: 'The console fixtures are read-only.' }, 400);
  if (mode.has('failing'))
    return json({ error: 'unavailable', message: 'The data service is unavailable.' }, 503);
  const list = lists[path];
  if (list) return json({ items: mode.has('empty') ? [] : list, next: null });
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
