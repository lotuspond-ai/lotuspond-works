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
    ],
  },
];

export function plannedArea(id: string) {
  return plannedAreas.find((area) => area.id === id);
}
