// Studio draft logic: a workflow package plus the positions of its nodes on the canvas.
// Everything here is pure so the canvas and inspector stay thin; studio.test.ts covers it.
import {
  IDENTIFIER,
  SCHEMA_VERSION,
  isRecord,
  readPackage,
  validatePackage,
  type AgentLimits,
  type Edge,
  type NodeKind,
  type PackageNode,
  type Problem,
  type Validation,
  type WorkflowPackage,
} from './packageContract.js';

export type Position = { x: number; y: number };
export type Layout = Record<string, Position>;
export type Draft = { package: WorkflowPackage; layout: Layout };

export const GRID = 8;
export const NODE_WIDTH = 176;
export const NODE_HEIGHT = 60;
const COLUMN_GAP = 72;
const ROW_GAP = 32;
const MARGIN = 24;

// The palette's order: the way a workflow reads, left to right.
export const NODE_KINDS_IN_ORDER: readonly NodeKind[] = [
  'start',
  'agent',
  'connector',
  'human_review',
  'end',
];
export const KIND_LABELS: Record<NodeKind, string> = {
  start: 'Start',
  end: 'End',
  agent: 'Agent',
  connector: 'Connector',
  human_review: 'Human review',
};
export const KIND_DESCRIPTIONS: Record<NodeKind, string> = {
  start: 'Where the workflow begins. One per package, with no incoming edges.',
  end: 'Where the workflow stops. At least one, with no outgoing edges.',
  agent: 'A bounded model step: declared model, written instructions and hard limits.',
  connector: 'One declared operation of a declared connector.',
  human_review: 'A person reviews before the workflow continues.',
};
// Starting limits for a new agent, matching contracts/examples/reviewed-action.v1.json.
export const DEFAULT_LIMITS: AgentLimits = {
  max_steps: 5,
  timeout_seconds: 60,
  max_input_tokens: 4000,
  max_output_tokens: 1000,
  max_cost_microusd: 100000,
};

export const snap = (value: number) => Math.max(0, Math.round(value / GRID) * GRID);

export function emptyPackage(): WorkflowPackage {
  return {
    schema_version: SCHEMA_VERSION,
    package_id: 'new-workflow',
    version: '0.1.0',
    name: 'New workflow',
    entry_node: 'start',
    models: [],
    connectors: [],
    secret_refs: [],
    nodes: [
      { id: 'start', kind: 'start' },
      { id: 'end', kind: 'end' },
    ],
    edges: [{ source: 'start', target: 'end' }],
  };
}

// Columns by distance from the entry node; nodes it cannot reach take the last column.
export function autoLayout(pkg: WorkflowPackage): Layout {
  const outgoing = new Map<string, string[]>(pkg.nodes.map((node) => [node.id, []]));
  for (const edge of pkg.edges) outgoing.get(edge.source)?.push(edge.target);
  const depth = new Map<string, number>();
  const queue = pkg.nodes.some((node) => node.id === pkg.entry_node) ? [pkg.entry_node] : [];
  if (queue.length) depth.set(pkg.entry_node, 0);
  while (queue.length) {
    const id = queue.shift() as string;
    for (const target of outgoing.get(id) ?? [])
      if (!depth.has(target)) {
        depth.set(target, (depth.get(id) ?? 0) + 1);
        queue.push(target);
      }
  }
  const deepest = Math.max(-1, ...depth.values()) + 1;
  const columns = new Map<number, string[]>();
  for (const node of pkg.nodes) {
    const column = depth.get(node.id) ?? deepest;
    columns.set(column, [...(columns.get(column) ?? []), node.id]);
  }
  const layout: Layout = {};
  for (const [column, ids] of columns)
    ids.forEach((id, row) => {
      layout[id] = {
        x: snap(MARGIN + column * (NODE_WIDTH + COLUMN_GAP)),
        y: snap(MARGIN + row * (NODE_HEIGHT + ROW_GAP)),
      };
    });
  return layout;
}

export function emptyDraft(): Draft {
  const pkg = emptyPackage();
  return { package: pkg, layout: autoLayout(pkg) };
}
export function draftFromPackage(pkg: WorkflowPackage): Draft {
  return { package: pkg, layout: autoLayout(pkg) };
}

export function canvasSize(layout: Layout) {
  const positions = Object.values(layout);
  return {
    width: Math.max(640, ...positions.map((p) => p.x + NODE_WIDTH + MARGIN * 2)),
    height: Math.max(360, ...positions.map((p) => p.y + NODE_HEIGHT + MARGIN * 2)),
  };
}

// 'agent', then 'agent-2', 'agent-3'…, never colliding with an existing identifier.
export function nextNodeId(pkg: WorkflowPackage, kind: NodeKind): string {
  const base = kind.replaceAll('_', '-');
  const taken = new Set(pkg.nodes.map((node) => node.id));
  if (!taken.has(base)) return base;
  for (let index = 2; ; index += 1) if (!taken.has(`${base}-${index}`)) return `${base}-${index}`;
}

// A free spot to the right of the last node, for adding without a pointer.
export function freePosition(layout: Layout): Position {
  const positions = Object.values(layout);
  if (!positions.length) return { x: MARGIN, y: MARGIN };
  const right = Math.max(...positions.map((p) => p.x));
  const rightmost = positions.filter((p) => p.x === right);
  const below = Math.max(...rightmost.map((p) => p.y)) + NODE_HEIGHT + ROW_GAP;
  return { x: snap(right), y: snap(below) };
}

export function newNode(pkg: WorkflowPackage, kind: NodeKind): PackageNode {
  const id = nextNodeId(pkg, kind);
  switch (kind) {
    case 'start':
      return { id, kind };
    case 'end':
      return { id, kind };
    case 'human_review':
      return { id, kind };
    case 'agent':
      return {
        id,
        kind,
        model_ref: pkg.models[0]?.id ?? '',
        instructions: '',
        limits: { ...DEFAULT_LIMITS },
      };
    case 'connector':
      return {
        id,
        kind,
        connector_ref: pkg.connectors[0]?.id ?? '',
        operation: pkg.connectors[0]?.operations[0] ?? '',
      };
    default: {
      const exhaustive: never = kind;
      return exhaustive;
    }
  }
}

// The entry node follows the single start node; two starts or none is left for the check.
function withEntry(pkg: WorkflowPackage): WorkflowPackage {
  const starts = pkg.nodes.filter((node) => node.kind === 'start');
  if (starts.length === 1 && pkg.entry_node !== starts[0].id)
    return { ...pkg, entry_node: starts[0].id };
  return pkg;
}

export function addNode(draft: Draft, kind: NodeKind, position?: Position): Draft {
  const node = newNode(draft.package, kind);
  const at = position ?? freePosition(draft.layout);
  return {
    package: withEntry({ ...draft.package, nodes: [...draft.package.nodes, node] }),
    layout: { ...draft.layout, [node.id]: { x: snap(at.x), y: snap(at.y) } },
  };
}

export function removeNode(draft: Draft, id: string): Draft {
  const { [id]: _removed, ...layout } = draft.layout;
  return {
    package: withEntry({
      ...draft.package,
      nodes: draft.package.nodes.filter((node) => node.id !== id),
      edges: draft.package.edges.filter((edge) => edge.source !== id && edge.target !== id),
    }),
    layout,
  };
}

export function updateNode(draft: Draft, id: string, node: PackageNode): Draft {
  return {
    ...draft,
    package: withEntry({
      ...draft.package,
      nodes: draft.package.nodes.map((item) => (item.id === id ? node : item)),
    }),
  };
}

// Renaming keeps every edge, the entry node and the position pointing at the node.
export function renameNode(draft: Draft, id: string, nextId: string): Draft {
  if (id === nextId || draft.package.nodes.some((node) => node.id === nextId)) return draft;
  const { [id]: position, ...rest } = draft.layout;
  const rename = (value: string) => (value === id ? nextId : value);
  return {
    package: {
      ...draft.package,
      entry_node: rename(draft.package.entry_node),
      nodes: draft.package.nodes.map((node) => (node.id === id ? { ...node, id: nextId } : node)),
      edges: draft.package.edges.map((edge) => ({
        source: rename(edge.source),
        target: rename(edge.target),
      })),
    },
    layout: position ? { ...rest, [nextId]: position } : rest,
  };
}

export function moveNode(draft: Draft, id: string, position: Position): Draft {
  return {
    ...draft,
    layout: { ...draft.layout, [id]: { x: snap(position.x), y: snap(position.y) } },
  };
}

export const sameEdge = (a: Edge, b: Edge) => a.source === b.source && a.target === b.target;

// A self-loop or a repeated edge is never what a drag meant, so neither is added.
export function addEdge(draft: Draft, edge: Edge): Draft {
  if (edge.source === edge.target || draft.package.edges.some((item) => sameEdge(item, edge)))
    return draft;
  return { ...draft, package: { ...draft.package, edges: [...draft.package.edges, edge] } };
}
export function removeEdge(draft: Draft, edge: Edge): Draft {
  return {
    ...draft,
    package: {
      ...draft.package,
      edges: draft.package.edges.filter((item) => !sameEdge(item, edge)),
    },
  };
}

export function updatePackage(draft: Draft, patch: Partial<WorkflowPackage>): Draft {
  return { ...draft, package: { ...draft.package, ...patch } };
}

// Browser storage holds the draft and layout. A draft in progress rarely satisfies the
// contract yet, so the shape is read leniently and the admission check reports the rest.
const text = (value: unknown) => (typeof value === 'string' ? value : '');
const integer = (value: unknown) =>
  typeof value === 'number' && Number.isInteger(value) ? value : Number.NaN;
const records = (value: unknown): Record<string, unknown>[] =>
  Array.isArray(value) ? value.filter(isRecord) : [];
function looseNode(raw: Record<string, unknown>): PackageNode | null {
  const id = text(raw.id);
  switch (raw.kind) {
    case 'start':
      return { id, kind: 'start' };
    case 'end':
      return { id, kind: 'end' };
    case 'human_review':
      return { id, kind: 'human_review' };
    case 'connector':
      return {
        id,
        kind: 'connector',
        connector_ref: text(raw.connector_ref),
        operation: text(raw.operation),
      };
    case 'agent': {
      const limits = isRecord(raw.limits) ? raw.limits : {};
      return {
        id,
        kind: 'agent',
        model_ref: text(raw.model_ref),
        instructions: text(raw.instructions),
        limits: {
          max_steps: integer(limits.max_steps),
          timeout_seconds: integer(limits.timeout_seconds),
          max_input_tokens: integer(limits.max_input_tokens),
          max_output_tokens: integer(limits.max_output_tokens),
          max_cost_microusd: integer(limits.max_cost_microusd),
        },
      };
    }
    default:
      return null;
  }
}
export function loosePackage(raw: unknown): WorkflowPackage | null {
  if (!isRecord(raw)) return null;
  const nodes = records(raw.nodes).flatMap((node) => looseNode(node) ?? []);
  return {
    schema_version: SCHEMA_VERSION,
    package_id: text(raw.package_id),
    version: text(raw.version),
    name: text(raw.name),
    entry_node: text(raw.entry_node),
    models: records(raw.models).map((model) => ({
      id: text(model.id),
      profile: text(model.profile),
      revision: text(model.revision),
    })),
    connectors: records(raw.connectors).map((connector) => ({
      id: text(connector.id),
      connector: text(connector.connector),
      version: text(connector.version),
      secret_ref: typeof connector.secret_ref === 'string' ? connector.secret_ref : null,
      operations: Array.isArray(connector.operations)
        ? connector.operations.filter((item): item is string => typeof item === 'string')
        : [],
    })),
    secret_refs: records(raw.secret_refs).map((secret) => ({ id: text(secret.id) })),
    nodes,
    edges: records(raw.edges).map((edge) => ({
      source: text(edge.source),
      target: text(edge.target),
    })),
  };
}
export function readDraft(raw: unknown): Draft | null {
  if (!isRecord(raw)) return null;
  // A fully valid package is read strictly, so stored content never gains extra fields.
  const pkg = readPackage(raw.package).package ?? loosePackage(raw.package);
  if (!pkg || !pkg.nodes.length) return null;
  const positions = isRecord(raw.layout) ? raw.layout : {};
  const layout: Layout = {};
  for (const node of pkg.nodes) {
    const position = positions[node.id];
    layout[node.id] =
      isRecord(position) &&
      typeof position.x === 'number' &&
      typeof position.y === 'number' &&
      Number.isFinite(position.x) &&
      Number.isFinite(position.y)
        ? { x: snap(position.x), y: snap(position.y) }
        : { x: MARGIN, y: MARGIN };
  }
  return { package: pkg, layout };
}
export function parseDraft(stored: string | null): Draft | null {
  if (!stored) return null;
  try {
    return readDraft(JSON.parse(stored));
  } catch {
    return null;
  }
}

// A draft in memory can hold values JSON never carries (a cleared number is NaN), so its
// fields are checked first; the server order in validatePackage then applies to the rest.
export function checkDraft(pkg: WorkflowPackage): Validation {
  const read = readPackage(pkg);
  if (!read.package) return { ok: false, problems: read.problems };
  return validatePackage(read.package);
}

export const isIdentifier = (value: string) => IDENTIFIER.test(value) && value.length <= 64;

// Splits "read-case, write-case" into the list the contract expects.
export function parseOperations(text: string): string[] {
  return text
    .split(/[\s,]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

// A readable location for a problem, such as "nodes › 3 › agent › instructions".
export function problemLocation(problem: Problem): string {
  if (problem.path.length)
    return problem.path.map((part) => String(part).replaceAll('_', ' ')).join(' › ');
  return problem.nodes.length ? problem.nodes.join(', ') : 'Package';
}
