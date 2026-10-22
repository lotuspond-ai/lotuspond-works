// Planned console areas. Each entry traces to docs/plans/2026-10-13-platform-master-plan.md and
// the dependency-ordered epics in docs/plans/2026-10-13-platform-delivery.md, and to a planned
// row or remaining work in docs/CAPABILITIES.md. Move an area out of this list only when that
// register records it as implemented; these pages describe intent, never a working feature.

export type PlannedArea = {
  id: string;
  section: string;
  title: string;
  summary: string;
  will: readonly string[];
  dependsOn: readonly string[];
  acceptance: string;
  // What already exists underneath, outside this console page. Stated only as CAPABILITIES.md does.
  foundation?: string;
  today: readonly { href: string; label: string; note: string }[];
};

export const plannedAreas: readonly PlannedArea[] = [
  {
    id: 'control-room',
    section: 'Operate',
    title: 'Control Room',
    summary:
      'Deploy published workflow versions and run them in production environments you control, with the queues, schedules and workers that keep work moving.',
    will: [
      'Admit a published version only when its signature matches publisher trust you configure.',
      'Deploy a version to an environment, with work already in progress pinned to the version it started on.',
      'Enroll, revoke and watch customer-side workers that run each step inside your boundary.',
      'Queues, schedules and triggers that admit a delivered trigger once, even when it arrives twice.',
      'Pause, cancel and recover executions after a worker, provider or network failure, reconciling any uncertain external effect before a retry.',
    ],
    dependsOn: [
      'Package contract and SDK (E02)',
      'Identity, connections and fleet (E03)',
      'Durable execution (E04)',
    ],
    acceptance:
      'Restart, timers, duplicate admission, cancellation and pinned-version behavior demonstrated before production runs are offered.',
    foundation:
      'A separately installed Linux runtime can enroll as a machine with its own expiring sessions and credential rotation through an opt-in API. It receives no business work yet.',
    today: [
      {
        href: '#studio',
        label: 'Studio',
        note: 'Compose, check and publish the immutable package version a deployment will run.',
      },
      {
        href: '#runs',
        label: 'Evaluation runs',
        note: 'Evaluate an agent against frozen scenarios. An evaluation run is not a production execution.',
      },
      {
        href: '#traces',
        label: 'Traces',
        note: 'Receive production evidence from an agent you already run yourself.',
      },
    ],
  },
  {
    id: 'connections',
    section: 'Build',
    title: 'Connections',
    summary:
      'Authorize the business systems a workflow may use, through credential references instead of stored secrets, and govern the knowledge it may read.',
    will: [
      'Connect a business system once per environment and reference it from packages by name.',
      'Resolve credentials at run time from your secret store; packages only ever hold reference names.',
      'Limit each connection to the operations a deployed package declares and you approve.',
      'Offer qualified connectors with stated versions, operations, idempotency and reconciliation tests.',
      'Version knowledge sources with access rules, so retrieval respects who may read what.',
    ],
    dependsOn: ['Identity, connections and fleet (E03)', 'Durable execution (E04)'],
    acceptance:
      'Tenant and environment isolation, worker enrollment and revocation, and secret-reference resolution proven, with each connector qualified individually.',
    foundation:
      'Owners and administrators can already manage immutable connection configurations, secret-reference metadata and runtime pool policies through an opt-in API. It grants no business execution authority.',
    today: [
      {
        href: '#studio',
        label: 'Studio',
        note: 'Declare connector references, versions, operations and secret reference names in a package.',
      },
      {
        href: '#integrations',
        label: 'Integrations',
        note: 'Register agent, telemetry and delivery endpoints for evaluation. Registration is not a verified connection.',
      },
    ],
  },
  {
    id: 'human-tasks',
    section: 'Operate',
    title: 'Human tasks',
    summary:
      'One inbox for the reviews, approvals and exceptions a running workflow waits on, with each decision tied to the exact action it allows.',
    will: [
      'Show each waiting case with the exact action and inputs a person is asked to approve.',
      'Invalidate an approval when its parameters change or it expires, so a stale decision cannot authorize a different action.',
      'Route exceptions and escalations to the right people with deadlines.',
      'Link every decision to the deployed version, the action taken and the downstream outcome.',
    ],
    dependsOn: [
      'Durable execution (E04)',
      'Actions and human tasks (E05)',
      'Cases and verified outcomes (E06)',
    ],
    acceptance:
      'Changed parameters or expiry invalidate an approval, a denied action cannot execute, and uncertain effects reconcile before anything is retried.',
    today: [
      {
        href: '#approvals',
        label: 'Approvals',
        note: 'Independent approval of release decisions and high-risk actions by a different administrator.',
      },
      {
        href: '#incidents',
        label: 'Incidents',
        note: 'Follow failures and unavailable assessments raised from production evidence.',
      },
    ],
  },
];

export type StudioStage = {
  name: string;
  state: 'available' | 'planned';
  text: string;
  link?: { href: string; label: string };
};

// Where a Studio package goes after the canvas. Each state traces to docs/CAPABILITIES.md; a
// planned stage links to its Planned page above, and moves only when that register does.
export const studioStages: readonly StudioStage[] = [
  {
    name: 'Compose',
    state: 'available',
    text: 'Draw the workflow on the canvas from a pattern, a file or a blank start. Drafts stay in this browser.',
  },
  {
    name: 'Check',
    state: 'available',
    text: 'The admission check mirrors the registry’s v1 validator as you work and shows the content digest.',
  },
  {
    name: 'Publish',
    state: 'available',
    text: 'Store an immutable version in the project’s registry. Publishing does not approve, sign or run it.',
  },
  {
    name: 'Evaluate agents',
    state: 'available',
    text: 'Test an agent you run against frozen scenarios and gate its release. An evaluation run does not run the package.',
    link: { href: '#runs', label: 'Evaluation runs' },
  },
  {
    name: 'Deploy and run',
    state: 'planned',
    text: 'Admit a signed version to an environment and run it on workers you control.',
    link: { href: '#control-room', label: 'Control Room' },
  },
  {
    name: 'Operate',
    state: 'planned',
    text: 'Work the reviews and exceptions a running workflow waits on, each tied to the version that produced it.',
    link: { href: '#human-tasks', label: 'Human tasks' },
  },
];

export function plannedArea(id: string) {
  return plannedAreas.find((area) => area.id === id);
}
