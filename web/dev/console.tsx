// Development-only visual harness for the signed-in console. Vite serves it at
// /dev/console.html during `npm run dev:web`; `vite build` only builds web/index.html, so it
// never ships. It answers the console's API requests from the fixtures below, so layouts can
// be reviewed without an account or a database. Append `?empty` to see every list empty,
// `?failing` to see every list fail, or `?viewer` to see every workspace as a read-only
// member, whose writes the fixtures refuse as the API does, or `?unwired` to see the execution
// routes answer as a server without production execution does. Fixture records are fictional and
// never leave this page.
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
    {
      id: 'e2',
      project_id: project.id,
      name: 'Production',
      kind: 'production',
      created_at: ago(7900),
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
      capabilities: ['http'],
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

// Execution fixtures for the Operate pages, answered under /projects/{p}/environments/{e}/.
const at = (minutes: number) => Date.now() + minutes * 60_000;
const digest = (seed: string) => seed.repeat(64).slice(0, 64);
const tasks = [
  {
    id: 't-refund',
    execution_id: '0193a7e2-5f10-7c3a-9b1e-4d2f8a6c1e01',
    kind: 'review',
    state: 'open',
    deadline_ms: at(42),
    escalated: true,
    escalate_at_ms: at(-5),
    claim: 'none',
    claim_expires_at_ms: null,
    intent_sha256: digest('a1'),
  },
  {
    id: 't-credit',
    execution_id: '0193a7e5-0a44-7d21-8c03-91be27f4d5a8',
    kind: 'review',
    state: 'open',
    deadline_ms: at(180),
    escalated: false,
    escalate_at_ms: at(90),
    claim: 'other',
    claim_expires_at_ms: at(12),
    intent_sha256: digest('b2'),
  },
  {
    id: 't-address',
    execution_id: '0193a7e9-7c2b-7e90-a1f4-0c6d3b9e2f77',
    kind: 'form',
    state: 'open',
    deadline_ms: at(600),
    escalated: false,
    escalate_at_ms: null,
    claim: 'none',
    claim_expires_at_ms: null,
    intent_sha256: null,
  },
] as (Record<string, unknown> & { id: string })[];
const proposals: Record<string, Record<string, unknown>> = {
  't-refund': {
    intent_sha256: digest('a1'),
    connector_name: 'payments',
    connector_version: '2.1.0',
    operation: 'issue_refund',
    policy_name: 'refunds-above-limit',
    policy_version: '3',
    approval_mode: 'human',
    independent_approval: true,
    requester_id: 'u-agent-owner',
    issued_at_ms: at(-20),
    expires_at_ms: at(40),
    target: { order: 'ORD-55120', customer: 'C-1083' },
    input: { amount: '184.00', currency: 'EUR', reason: 'Damaged on arrival' },
  },
  't-credit': {
    intent_sha256: digest('b2'),
    connector_name: 'payments',
    connector_version: '2.1.0',
    operation: 'issue_store_credit',
    policy_name: 'store-credit',
    policy_version: '1',
    approval_mode: 'human',
    independent_approval: false,
    requester_id: 'u-agent-owner',
    issued_at_ms: at(-3),
    expires_at_ms: at(177),
    target: { customer: 'C-2210' },
    input: { amount: '25.00', currency: 'EUR' },
  },
};
const deployments = [
  {
    id: '0193a6f0-12ab-7c4d-8e5f-6a7b8c9d0e1f',
    revision_id: '0193a6f1-7c2e-7a1b-9c8d-e7f6a5b4c3d2',
    status: 'enabled',
    unreviewed_operations: [],
  },
  {
    id: '0193a1c4-98fe-7d6c-a5b4-3c2d1e0f9a8b',
    revision_id: '0193a1c5-11aa-7b2c-8d3e-f4a5b6c7d8e9',
    status: 'disabled',
    unreviewed_operations: ['orders/annotate'],
  },
];
const executions = [
  ['0193a7e2-5f10-7c3a-9b1e-4d2f8a6c1e01', 'waiting', 'running', null, -21],
  ['0193a7e5-0a44-7d21-8c03-91be27f4d5a8', 'waiting', 'running', null, -4],
  ['0193a7e9-7c2b-7e90-a1f4-0c6d3b9e2f77', 'waiting', 'running', null, -2],
  ['0193a7d0-3e8f-7a55-b2c9-6f1a0d4e8c32', 'running', 'paused', null, -1],
  ['0193a790-91d2-7b08-8e6a-2c5f7b1d9a14', 'succeeded', 'succeeded', null, -95],
  ['0193a702-4b67-7f13-9d28-8a0e3c6f1b59', 'failed', 'failed', 'activity_failed', -240],
].map(([id, state, requested_state, reason, minutes]) => ({
  id,
  state,
  requested_state,
  reason,
  admitted_at_ms: at(minutes as number),
  deadline_ms: at((minutes as number) + 1440),
  result: null,
  next_event_sequence: 9,
})) as (Record<string, unknown> & { id: string })[];
const refundRuns = new Set([
  '0193a7e2-5f10-7c3a-9b1e-4d2f8a6c1e01',
  '0193a790-91d2-7b08-8e6a-2c5f7b1d9a14',
  '0193a702-4b67-7f13-9d28-8a0e3c6f1b59',
]);
const events = (id: string) =>
  ['admitted', 'step_started', 'activity_completed', 'intent_recorded', 'task_opened'].map(
    (code, index) => ({
      sequence: index + 1,
      family: 'engine',
      code,
      occurred_at_ms: at(-20 + index * 2) + id.length,
      attempt_id: null,
      application_state: null,
    }),
  );

const asha = { user_id: 'u2', name: 'Asha Rao' };
const caseRuns = (ids: string[]) =>
  ids
    .map((id) => executions.find((item) => item.id === id)!)
    .map((run) => ({
      id: run.id,
      state: run.state,
      admitted_at_ms: run.admitted_at_ms as number,
      outcome:
        run.state === 'succeeded'
          ? 'verified'
          : run.state === 'failed'
            ? 'no_effect'
            : 'in_progress',
      exception: run.state === 'failed',
    }));
const cases = [
  {
    id: '0193a800-0001-7000-8000-000000000001',
    key: 'ORD-55120',
    runs: ['0193a702-4b67-7f13-9d28-8a0e3c6f1b59', '0193a7e2-5f10-7c3a-9b1e-4d2f8a6c1e01'],
    owner: null as null | Record<string, unknown>,
    resolution: null as null | Record<string, unknown>,
    needs: true,
  },
  {
    id: '0193a800-0002-7000-8000-000000000002',
    key: 'ORD-55098',
    runs: ['0193a790-91d2-7b08-8e6a-2c5f7b1d9a14'],
    owner: null,
    resolution: null,
    needs: false,
  },
  {
    id: '0193a800-0003-7000-8000-000000000003',
    key: 'ORD-54977',
    runs: [],
    owner: { ...asha, since_ms: at(-30) },
    resolution: null,
    needs: false,
  },
];
const caseView = (item: (typeof cases)[number], full = true) => {
  const runs = caseRuns(item.runs).sort((a, b) => b.admitted_at_ms - a.admitted_at_ms);
  return {
    id: item.id,
    key: item.key,
    key_sha256: digest('9a'),
    created_at: new Date(at(-300)).toISOString(),
    outcome: runs[0]?.outcome ?? 'empty',
    needs_person: item.needs,
    owner: item.owner,
    resolution: item.resolution,
    execution_count: runs.length,
    ...(full ? { executions: runs } : {}),
  };
};

function execution(path: string, method: string, body: Record<string, unknown>) {
  if (mode.has('unwired'))
    return json(
      { detail: { error: 'authority_unavailable', message: 'authority_unavailable' } },
      503,
    );
  const page = <T,>(items: T[]) =>
    json({ items: mode.has('empty') ? [] : items, next_after_id: null });
  if (path === 'human-tasks') return page(tasks.filter((task) => task.state === 'open'));
  if (path === 'deployments') return page(deployments);
  if (path === 'executions') return page(executions);
  // Studio v2: the reviewed-action pattern's connector is published here; its policy is not.
  if (path.startsWith('executable-packages') && method === 'GET' && !path.includes('/'))
    return page([
      {
        id: '0193a900-0001-7000-8000-000000000001',
        kind: 'connector',
        name: 'connector-fixture',
        version: '1.0.0',
        package_sha256: '478fde8e7f65a29046477b92f4110126d77aff896e45e5686213e47f720ce76d',
        plan_sha256: null,
        validator_id: 'fixture',
        compiler_id: null,
      },
    ]);
  if (path === 'executable-packages/check' && method === 'POST') {
    // The fixture stands in for the server's SDK check: a version must be x.y.z.
    const pkg = body.package as Record<string, unknown>;
    const problems = /^\d+\.\d+\.\d+$/.test(String(pkg?.version))
      ? []
      : [
          {
            path: '/version',
            code: 'invalid_package',
            message: 'The executable package is invalid.',
          },
        ];
    const canonical = JSON.stringify(pkg);
    return json({
      valid: problems.length === 0,
      package_sha256: digest('7e'),
      plan_sha256: problems.length ? null : digest('8f'),
      canonical,
      problems,
    });
  }
  if (path === 'cases' && method === 'GET') return page(cases.map((item) => caseView(item, false)));
  if (path === 'cases' && method === 'POST') {
    const found = cases.find((item) => item.key === body.key);
    if (found) return json(caseView(found));
    const created = {
      id: `0193a800-00${cases.length + 1}0-7000-8000-00000000000${cases.length + 1}`,
      key: String(body.key),
      runs: [],
      owner: null,
      resolution: null,
      needs: false,
    };
    cases.push(created);
    return json(caseView(created));
  }
  const kase = /^cases\/([^/]+)(?:\/(\w+))?$/.exec(path);
  if (kase) {
    const found = cases.find((item) => item.id === kase[1]);
    if (!found) return json({ error: 'not_found', message: 'not_found' }, 404);
    if (method === 'POST' && role === 'viewer')
      return json({ error: 'forbidden', message: 'forbidden' }, 403);
    if (kase[2] === 'owner')
      found.owner =
        body.action === 'take' ? { user_id: 'u1', name: 'You', since_ms: Date.now() } : null;
    if (kase[2] === 'resolution') {
      found.resolution = {
        kind: body.kind,
        resolved_by: { user_id: 'u1', name: 'You' },
        resolved_at_ms: Date.now(),
      };
      found.needs = false;
    }
    if (kase[2] === 'executions' && !found.runs.includes(String(body.execution_id)))
      found.runs.push(String(body.execution_id));
    return json(caseView(found));
  }
  if (path.startsWith('outcomes'))
    return json({
      since_ms: 0,
      deployment_id: null,
      total: executions.length,
      truncated: false,
      outcomes: {
        in_progress: 4,
        unknown: 0,
        not_verified: 0,
        verified: 1,
        completed: 0,
        partial: 0,
        no_effect: 1,
      },
      exceptions: 1,
    });
  const task = /^human-tasks\/([^/]+)(?:\/(\w+))?$/.exec(path);
  if (task) {
    const found = tasks.find((item) => item.id === task[1]);
    if (!found) return json({ detail: { error: 'not_found', message: 'not_found' } }, 404);
    if (method === 'GET') return task[2] === 'proposal' ? json(proposals[found.id]) : json(found);
    if (role === 'viewer')
      return json({ detail: { error: 'forbidden', message: 'forbidden' } }, 403);
    if (task[2] === 'claim') Object.assign(found, { claim: 'caller', claim_expires_at_ms: at(15) });
    if (task[2] === 'release') Object.assign(found, { claim: 'none', claim_expires_at_ms: null });
    if (task[2] === 'decision') {
      if (body.intent_sha256 !== found.intent_sha256)
        return json({ detail: { error: 'conflict', message: 'conflict' } }, 409);
      found.state = 'decided';
    }
    if (task[2] === 'submission') found.state = 'submitted';
    return json(found);
  }
  const run = /^executions\/([^/]+)(?:\/(\w+))?(?:\/export)?$/.exec(path);
  const found = run && executions.find((item) => item.id === run[1]);
  if (!run || !found) return json({ detail: { error: 'not_found', message: 'not_found' } }, 404);
  if (!run[2]) return json(found);
  if (run[2] === 'trace') {
    const done = found.state === 'succeeded';
    const refund = refundRuns.has(found.id);
    const state =
      found.state === 'succeeded'
        ? 'verified'
        : found.state === 'failed'
          ? 'no_effect'
          : 'in_progress';
    return json({
      format: 'lotuspond.execution-trace.v1',
      execution: { id: found.id, state: found.state },
      version: {
        deployment_id: '0193a6f0-12ab-7c4d-8e5f-6a7b8c9d0e1f',
        deployment_revision_id: '0193a6f1-7c2e-7a1b-9c8d-e7f6a5b4c3d2',
        revision: 3,
        package: 'refund-assistant',
        package_version: '1.4.0',
        package_sha256: digest('c4'),
        release_id: 'release-fixture',
      },
      actions: refund
        ? [
            {
              action_id: `a-${found.id}`,
              step_id: 'issue_refund',
              connector: { name: 'payments', version: '2.1.0' },
              operation: 'issue_refund',
              approval_mode: 'human',
              authorization: done ? 'approved' : 'awaiting_review',
              effect: done ? 'observed' : 'not_dispatched',
              outcome: done ? 'verified' : 'unknown',
              review: {
                task_id: 't-refund',
                state: done ? 'decided' : 'open',
                decision: done
                  ? {
                      decision: 'allow',
                      decided_by: { user_id: 'u2', name: 'Asha Rao' },
                      issued_at_ms: at(-90),
                    }
                  : null,
              },
              verification: done
                ? { state: 'verified', rule_id: 'refund_recorded', observed_at_ms: at(-89) }
                : null,
            },
          ]
        : [],
      outcome: { state, exception: found.state === 'failed' },
      trace_sha256: digest('d5'),
    });
  }
  if (run[2] === 'events') return json({ items: events(found.id), next_after_sequence: null });
  if (run[2] === 'waits')
    return page(
      found.state === 'waiting'
        ? [
            {
              id: `w-${found.id}`,
              occurrence_path: 'review_refund',
              state: 'open',
              deadline_ms: at(42),
            },
          ]
        : [],
    );
  if (run[2] === 'actions')
    return json({
      items: refundRuns.has(found.id)
        ? [
            {
              action_id: `a-${found.id}`,
              step_id: 'issue_refund',
              occurrence_path: 'issue_refund',
              intent_sha256: digest('a1'),
              authorization: found.state === 'succeeded' ? 'allowed' : 'pending_review',
              effect: found.state === 'succeeded' ? 'observed' : 'not_dispatched',
              outcome: found.state === 'succeeded' ? 'verified' : 'unknown',
              task_id: 't-refund',
              receipt_status: null,
              evidence: [],
            },
          ]
        : [],
    });
  if (run[2] === 'control' && method === 'POST') {
    if (role === 'viewer')
      return json({ detail: { error: 'forbidden', message: 'forbidden' } }, 403);
    const action = body.action;
    found.requested_state =
      action === 'pause' ? 'paused' : action === 'resume' ? 'running' : 'cancelled';
    return json(found);
  }
  return json({ detail: { error: 'not_found', message: 'not_found' } }, 404);
}

const settings = { name: 'Example organization' };

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
  const scoped = /^projects\/[^/]+\/environments\/[^/]+\/(.+)$/.exec(path);
  if (scoped) {
    let body: Record<string, unknown> = {};
    try {
      body = JSON.parse(typeof init?.body === 'string' ? init.body : '{}');
    } catch {
      /* A malformed body is answered by the route's own checks. */
    }
    return execution(scoped[1], method, body);
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
