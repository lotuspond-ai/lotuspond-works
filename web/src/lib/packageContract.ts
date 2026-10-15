// Local port of the shared v1 validator (sdk/python/src/lotuspond_sdk/contract.py, re-exported
// by backend/lotuspond/package_contract.py) for the Studio's admission preview. It checks the declared workflow-package v1 contract and computes the content digest. It
// does not install, sign, authorize, or run a package, and the server validator decides
// admission; packageContract.test.ts keeps the codes, messages and digest in step.

export const SCHEMA_VERSION = 'lotuspond.workflow-package.v1';
export const MAX_PACKAGE_BYTES = 1024 * 1024;
export const MAX_JSON_DEPTH = 32;
export const MAX_JSON_ITEMS = 10000;

export const IDENTIFIER = /^[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*$/;
export const VERSION = /^(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})$/;
export const REFERENCE_REVISION = /^[A-Za-z0-9][A-Za-z0-9._:/-]*$/;

export const BOUNDS = {
  identifier: { min: 1, max: 64 },
  version: { min: 0, max: 20 },
  revision: { min: 1, max: 128 },
  name: { min: 1, max: 160 },
  instructions: { min: 1, max: 16384 },
  models: { min: 0, max: 32 },
  connectors: { min: 0, max: 32 },
  secret_refs: { min: 0, max: 32 },
  operations: { min: 1, max: 32 },
  nodes: { min: 2, max: 128 },
  edges: { min: 1, max: 256 },
} as const;

export const NODE_KINDS = ['start', 'end', 'agent', 'connector', 'human_review'] as const;
export type NodeKind = (typeof NODE_KINDS)[number];

export type AgentLimits = {
  max_steps: number;
  timeout_seconds: number;
  max_input_tokens: number;
  max_output_tokens: number;
  max_cost_microusd: number;
};
export const LIMIT_RANGES: Record<keyof AgentLimits, { min: number; max: number }> = {
  max_steps: { min: 1, max: 100 },
  timeout_seconds: { min: 1, max: 3600 },
  max_input_tokens: { min: 1, max: 1_000_000 },
  max_output_tokens: { min: 1, max: 100_000 },
  max_cost_microusd: { min: 1, max: 1_000_000_000 },
};
const LIMIT_FIELDS = Object.keys(LIMIT_RANGES) as (keyof AgentLimits)[];

export type StartNode = { id: string; kind: 'start' };
export type EndNode = { id: string; kind: 'end' };
export type AgentNode = {
  id: string;
  kind: 'agent';
  model_ref: string;
  instructions: string;
  limits: AgentLimits;
};
export type ConnectorNode = {
  id: string;
  kind: 'connector';
  connector_ref: string;
  operation: string;
};
export type HumanReviewNode = { id: string; kind: 'human_review' };
export type PackageNode = StartNode | EndNode | AgentNode | ConnectorNode | HumanReviewNode;
export type ModelReference = { id: string; profile: string; revision: string };
export type ConnectorReference = {
  id: string;
  connector: string;
  version: string;
  secret_ref: string | null;
  operations: string[];
};
export type SecretReference = { id: string };
export type Edge = { source: string; target: string };
export type WorkflowPackage = {
  schema_version: typeof SCHEMA_VERSION;
  package_id: string;
  version: string;
  name: string;
  entry_node: string;
  models: ModelReference[];
  connectors: ConnectorReference[];
  secret_refs: SecretReference[];
  nodes: PackageNode[];
  edges: Edge[];
};

// Quoted from the server; the test asserts every entry appears in the SDK's contract.py.
export const ERROR_MESSAGES = {
  input_too_large: 'Package input exceeds the byte limit.',
  input_too_deep: 'Package input exceeds the nesting limit.',
  input_too_complex: 'Package input exceeds the item limit.',
  invalid_json_value: 'Package input must contain only JSON values.',
  invalid_unicode: 'Package input contains invalid Unicode.',
  nonfinite_number: 'Package input must not contain nonfinite numbers.',
  cyclic_input: 'Package input must not contain cyclic objects.',
  duplicate_identifier: 'Identifiers must be unique within each declaration list.',
  duplicate_operation: 'Connector operations must be unique.',
  unknown_model_reference: 'An agent references an undeclared model.',
  unknown_connector_reference: 'A node references an undeclared connector.',
  unknown_secret_reference: 'A connector references an undeclared secret reference.',
  unknown_operation: 'A node operation is not declared by its connector.',
  invalid_entry: 'Exactly one start node must match the entry node and have no incoming edges.',
  missing_terminal: 'The package must declare at least one end node.',
  unknown_edge_node: 'Every edge must reference declared nodes.',
  duplicate_edge: 'Edges must be unique.',
  terminal_outgoing: 'End nodes must not have outgoing edges.',
  nonterminal_dead_end: 'Every nonterminal node must have an outgoing edge.',
  unreachable_node: 'Every node must be reachable from the entry node.',
  graph_cycle: 'The package graph must be acyclic.',
} as const;
export type ContractCode = keyof typeof ERROR_MESSAGES;

export type Problem = {
  // The server's error location: field names and list indexes, empty for whole-graph problems.
  path: (string | number)[];
  code: string;
  message: string;
  // Node identifiers the problem concerns, so a canvas can mark them.
  nodes: string[];
};
export type Validation =
  | { ok: true; package: WorkflowPackage; canonical: string; problems: [] }
  | { ok: false; problems: Problem[] };

const PACKAGE_FIELDS = [
  'schema_version',
  'package_id',
  'version',
  'name',
  'entry_node',
  'models',
  'connectors',
  'secret_refs',
  'nodes',
  'edges',
] as const;
const NODE_FIELDS: Record<NodeKind, readonly string[]> = {
  start: ['id', 'kind'],
  end: ['id', 'kind'],
  agent: ['id', 'kind', 'model_ref', 'instructions', 'limits'],
  connector: ['id', 'kind', 'connector_ref', 'operation'],
  human_review: ['id', 'kind'],
};

const LONE_SURROGATE = /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/;
const encoder = new TextEncoder();

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

// Mirrors check_json_bounds: item and depth counting, then the compact byte size.
export function jsonBoundsProblem(raw: unknown): Problem | null {
  type Frame = { value: unknown; depth: number; exiting: boolean };
  const stack: Frame[] = [{ value: raw, depth: 0, exiting: false }];
  const active = new Set<object>();
  let count = 0;
  let bytes = 0;
  const reject = (code: ContractCode): Problem => ({
    path: [],
    code,
    message: ERROR_MESSAGES[code],
    nodes: [],
  });
  while (stack.length) {
    const { value, depth, exiting } = stack.pop() as Frame;
    if (exiting) {
      active.delete(value as object);
      continue;
    }
    count += 1;
    if (count > MAX_JSON_ITEMS) return reject('input_too_complex');
    if (depth > MAX_JSON_DEPTH) return reject('input_too_deep');
    if (typeof value === 'string') {
      if (value.length > MAX_PACKAGE_BYTES) return reject('input_too_large');
      if (LONE_SURROGATE.test(value)) return reject('invalid_unicode');
      bytes += encoder.encode(value).length;
    } else if (typeof value === 'number') {
      if (!Number.isFinite(value)) return reject('nonfinite_number');
    } else if (value === null || typeof value === 'boolean') {
      // Always a JSON value.
    } else if (typeof value === 'object') {
      if (active.has(value)) return reject('cyclic_input');
      active.add(value);
      stack.push({ value, depth, exiting: true });
      if (Array.isArray(value)) {
        for (const child of value) stack.push({ value: child, depth: depth + 1, exiting: false });
      } else {
        for (const [key, child] of Object.entries(value)) {
          stack.push({ value: key, depth: depth + 1, exiting: false });
          stack.push({ value: child, depth: depth + 1, exiting: false });
        }
      }
    } else {
      return reject('invalid_json_value');
    }
    if (bytes > MAX_PACKAGE_BYTES) return reject('input_too_large');
  }
  if (encoder.encode(JSON.stringify(raw)).length > MAX_PACKAGE_BYTES)
    return reject('input_too_large');
  return null;
}

type Collector = { problems: Problem[] };
type Path = (string | number)[];
const add = (c: Collector, path: Path, code: string, message: string, nodes: string[] = []) =>
  c.problems.push({ path, code, message, nodes });

function expectObject(c: Collector, value: unknown, path: Path, fields: readonly string[]) {
  if (!isRecord(value)) {
    add(c, path, 'model_type', 'Must be an object.');
    return null;
  }
  for (const key of Object.keys(value))
    if (!fields.includes(key))
      add(c, [...path, key], 'extra_forbidden', 'This field is not part of the contract.');
  return value;
}
type Pattern = { test: RegExp; message: string };
// The strict text check for one value already known to be present.
function checkString(
  c: Collector,
  value: unknown,
  at: Path,
  bounds: { min: number; max: number },
  pattern?: Pattern,
) {
  if (typeof value !== 'string') add(c, at, 'string_type', 'Must be text.');
  else if (value.length < bounds.min)
    add(
      c,
      at,
      'string_too_short',
      `Must be at least ${bounds.min} character${bounds.min === 1 ? '' : 's'}.`,
    );
  else if (value.length > bounds.max)
    add(c, at, 'string_too_long', `Must be at most ${bounds.max.toLocaleString()} characters.`);
  else if (pattern && !pattern.test.test(value))
    add(c, at, 'string_pattern_mismatch', pattern.message);
  else return value;
  return undefined;
}
function expectString(
  c: Collector,
  record: Record<string, unknown>,
  key: string,
  path: Path,
  bounds: { min: number; max: number },
  pattern?: Pattern,
) {
  const at = [...path, key];
  if (record[key] === undefined) {
    add(c, at, 'missing', 'This field is required.');
    return undefined;
  }
  return checkString(c, record[key], at, bounds, pattern);
}
const identifierPattern: Pattern = {
  test: IDENTIFIER,
  message:
    'Use lowercase letters and digits, joined by single hyphens or underscores, starting with a letter.',
};
const versionPattern: Pattern = {
  test: VERSION,
  message: 'Use a three-part version such as 1.0.0.',
};
const expectIdentifier = (c: Collector, record: Record<string, unknown>, key: string, path: Path) =>
  expectString(c, record, key, path, BOUNDS.identifier, identifierPattern);
function expectInteger(
  c: Collector,
  record: Record<string, unknown>,
  key: string,
  path: Path,
  range: { min: number; max: number },
) {
  const value = record[key];
  const at = [...path, key];
  if (value === undefined) add(c, at, 'missing', 'This field is required.');
  else if (typeof value !== 'number' || !Number.isInteger(value))
    add(c, at, 'int_type', 'Must be a whole number.');
  else if (value < range.min)
    add(
      c,
      at,
      'greater_than_equal',
      `Must be between ${range.min.toLocaleString()} and ${range.max.toLocaleString()}.`,
    );
  else if (value > range.max)
    add(
      c,
      at,
      'less_than_equal',
      `Must be between ${range.min.toLocaleString()} and ${range.max.toLocaleString()}.`,
    );
  else return value;
  return undefined;
}
function expectList(
  c: Collector,
  record: Record<string, unknown>,
  key: string,
  path: Path,
  bounds: { min: number; max: number },
) {
  const value = record[key];
  const at = [...path, key];
  if (value === undefined) add(c, at, 'missing', 'This field is required.');
  else if (!Array.isArray(value)) add(c, at, 'list_type', 'Must be a list.');
  else if (value.length < bounds.min)
    add(c, at, 'too_short', `Declare at least ${bounds.min} item${bounds.min === 1 ? '' : 's'}.`);
  else if (value.length > bounds.max)
    add(c, at, 'too_long', `Declare at most ${bounds.max} items.`);
  else return value as unknown[];
  return undefined;
}

function readModel(c: Collector, raw: unknown, path: Path): ModelReference | undefined {
  const record = expectObject(c, raw, path, ['id', 'profile', 'revision']);
  if (!record) return undefined;
  const id = expectIdentifier(c, record, 'id', path);
  const profile = expectIdentifier(c, record, 'profile', path);
  const revision = expectString(c, record, 'revision', path, BOUNDS.revision, {
    test: REFERENCE_REVISION,
    message: 'Use letters, digits and . _ : / - characters, starting with a letter or digit.',
  });
  return id !== undefined && profile !== undefined && revision !== undefined
    ? { id, profile, revision }
    : undefined;
}
function readConnector(c: Collector, raw: unknown, path: Path): ConnectorReference | undefined {
  const record = expectObject(c, raw, path, [
    'id',
    'connector',
    'version',
    'secret_ref',
    'operations',
  ]);
  if (!record) return undefined;
  const id = expectIdentifier(c, record, 'id', path);
  const connector = expectIdentifier(c, record, 'connector', path);
  const version = expectString(c, record, 'version', path, BOUNDS.version, versionPattern);
  let secret: string | null | undefined = null;
  if (record.secret_ref !== undefined && record.secret_ref !== null)
    secret = expectIdentifier(c, record, 'secret_ref', path);
  const list = expectList(c, record, 'operations', path, BOUNDS.operations);
  const operations = list?.map((item, index) =>
    checkString(c, item, [...path, 'operations', index], BOUNDS.identifier, identifierPattern),
  );
  let complete = operations !== undefined && operations.every((item) => item !== undefined);
  if (complete && new Set(operations).size !== operations?.length) {
    add(c, path, 'duplicate_operation', ERROR_MESSAGES.duplicate_operation);
    complete = false;
  }
  return complete &&
    id !== undefined &&
    connector !== undefined &&
    version !== undefined &&
    secret !== undefined
    ? { id, connector, version, secret_ref: secret, operations: operations as string[] }
    : undefined;
}
function readSecret(c: Collector, raw: unknown, path: Path): SecretReference | undefined {
  const record = expectObject(c, raw, path, ['id']);
  if (!record) return undefined;
  const id = expectIdentifier(c, record, 'id', path);
  return id !== undefined ? { id } : undefined;
}
function readLimits(c: Collector, raw: unknown, path: Path): AgentLimits | undefined {
  const record = expectObject(c, raw, path, LIMIT_FIELDS);
  if (!record) return undefined;
  const limits: Partial<AgentLimits> = {};
  for (const field of LIMIT_FIELDS) {
    const value = expectInteger(c, record, field, path, LIMIT_RANGES[field]);
    if (value !== undefined) limits[field] = value;
  }
  return LIMIT_FIELDS.every((field) => limits[field] !== undefined)
    ? (limits as AgentLimits)
    : undefined;
}
function readNode(c: Collector, raw: unknown, path: Path): PackageNode | undefined {
  if (!isRecord(raw)) {
    add(c, path, 'model_type', 'Must be an object.');
    return undefined;
  }
  const kind = raw.kind;
  if (kind === undefined) {
    add(c, [...path, 'kind'], 'union_tag_not_found', 'Each node needs a kind.');
    return undefined;
  }
  if (typeof kind !== 'string' || !(NODE_KINDS as readonly string[]).includes(kind)) {
    add(
      c,
      [...path, 'kind'],
      'union_tag_invalid',
      `Node kind must be one of ${NODE_KINDS.join(', ')}.`,
    );
    return undefined;
  }
  const nodeKind = kind as NodeKind;
  const at = [...path, nodeKind];
  const record = expectObject(c, raw, at, NODE_FIELDS[nodeKind]);
  if (!record) return undefined;
  const before = c.problems.length;
  const id = expectIdentifier(c, record, 'id', at);
  const mark = (problem: Problem) => {
    if (typeof raw.id === 'string' && !problem.nodes.length) problem.nodes = [raw.id];
  };
  let node: PackageNode | undefined;
  switch (nodeKind) {
    case 'start':
      node = id !== undefined ? { id, kind: 'start' } : undefined;
      break;
    case 'end':
      node = id !== undefined ? { id, kind: 'end' } : undefined;
      break;
    case 'human_review':
      node = id !== undefined ? { id, kind: 'human_review' } : undefined;
      break;
    case 'connector': {
      const connector_ref = expectIdentifier(c, record, 'connector_ref', at);
      const operation = expectIdentifier(c, record, 'operation', at);
      node =
        id !== undefined && connector_ref !== undefined && operation !== undefined
          ? { id, kind: 'connector', connector_ref, operation }
          : undefined;
      break;
    }
    case 'agent': {
      const model_ref = expectIdentifier(c, record, 'model_ref', at);
      const instructions = expectString(c, record, 'instructions', at, BOUNDS.instructions);
      const limits =
        record.limits === undefined
          ? (add(c, [...at, 'limits'], 'missing', 'This field is required.'), undefined)
          : readLimits(c, record.limits, [...at, 'limits']);
      node =
        id !== undefined &&
        model_ref !== undefined &&
        instructions !== undefined &&
        limits !== undefined
          ? { id, kind: 'agent', model_ref, instructions, limits }
          : undefined;
      break;
    }
    default: {
      const exhaustive: never = nodeKind;
      return exhaustive;
    }
  }
  c.problems.slice(before).forEach(mark);
  return node;
}
function readEdge(c: Collector, raw: unknown, path: Path): Edge | undefined {
  const record = expectObject(c, raw, path, ['source', 'target']);
  if (!record) return undefined;
  const source = expectIdentifier(c, record, 'source', path);
  const target = expectIdentifier(c, record, 'target', path);
  return source !== undefined && target !== undefined ? { source, target } : undefined;
}
function readList<T>(
  c: Collector,
  record: Record<string, unknown>,
  key: string,
  bounds: { min: number; max: number },
  read: (c: Collector, raw: unknown, path: Path) => T | undefined,
): T[] | undefined {
  const list = expectList(c, record, key, [], bounds);
  if (!list) return undefined;
  const items = list.map((item, index) => read(c, item, [key, index]));
  return items.every((item) => item !== undefined) ? (items as T[]) : undefined;
}

// Field-level checks with the server's strictness: exact types, no extra fields, bounds.
export function readPackage(raw: unknown): { package?: WorkflowPackage; problems: Problem[] } {
  const c: Collector = { problems: [] };
  const record = expectObject(c, raw, [], PACKAGE_FIELDS);
  if (!record) return { problems: c.problems };
  if (record.schema_version === undefined)
    add(c, ['schema_version'], 'missing', 'This field is required.');
  else if (record.schema_version !== SCHEMA_VERSION)
    add(c, ['schema_version'], 'literal_error', `Only ${SCHEMA_VERSION} is supported.`);
  const package_id = expectIdentifier(c, record, 'package_id', []);
  const version = expectString(c, record, 'version', [], BOUNDS.version, versionPattern);
  const name = expectString(c, record, 'name', [], BOUNDS.name);
  const entry_node = expectIdentifier(c, record, 'entry_node', []);
  const models = readList(c, record, 'models', BOUNDS.models, readModel);
  const connectors = readList(c, record, 'connectors', BOUNDS.connectors, readConnector);
  const secret_refs = readList(c, record, 'secret_refs', BOUNDS.secret_refs, readSecret);
  const nodes = readList(c, record, 'nodes', BOUNDS.nodes, readNode);
  const edges = readList(c, record, 'edges', BOUNDS.edges, readEdge);
  if (
    c.problems.length ||
    package_id === undefined ||
    version === undefined ||
    name === undefined ||
    entry_node === undefined ||
    !models ||
    !connectors ||
    !secret_refs ||
    !nodes ||
    !edges
  )
    return { problems: c.problems };
  return {
    problems: [],
    package: {
      schema_version: SCHEMA_VERSION,
      package_id,
      version,
      name,
      entry_node,
      models,
      connectors,
      secret_refs,
      nodes,
      edges,
    },
  };
}

// Graph invariants in the server's order, so problems[0] is the error the server reports.
// Every problem is collected, with the nodes it concerns, so a canvas can mark them all.
export function validateGraph(pkg: WorkflowPackage): Problem[] {
  const problems: Problem[] = [];
  const graph = (code: ContractCode, nodes: string[] = []) =>
    problems.push({ path: [], code, message: ERROR_MESSAGES[code], nodes });
  for (const list of [pkg.models, pkg.connectors, pkg.secret_refs, pkg.nodes]) {
    const seen = new Set<string>();
    const duplicates = new Set<string>();
    for (const { id } of list) (seen.has(id) ? duplicates : seen).add(id);
    if (duplicates.size) graph('duplicate_identifier', list === pkg.nodes ? [...duplicates] : []);
  }
  const models = new Set(pkg.models.map((model) => model.id));
  const secrets = new Set(pkg.secret_refs.map((secret) => secret.id));
  const connectors = new Map(pkg.connectors.map((connector) => [connector.id, connector]));
  const nodes = new Map(pkg.nodes.map((node) => [node.id, node]));
  for (const connector of pkg.connectors)
    if (connector.secret_ref !== null && !secrets.has(connector.secret_ref))
      graph('unknown_secret_reference');
  for (const node of pkg.nodes) {
    if (node.kind === 'agent' && !models.has(node.model_ref))
      graph('unknown_model_reference', [node.id]);
    if (node.kind === 'connector') {
      const connector = connectors.get(node.connector_ref);
      if (!connector) graph('unknown_connector_reference', [node.id]);
      else if (!connector.operations.includes(node.operation))
        graph('unknown_operation', [node.id]);
    }
  }
  const starts = pkg.nodes.filter((node) => node.kind === 'start').map((node) => node.id);
  const entryValid = starts.length === 1 && starts[0] === pkg.entry_node;
  if (!entryValid) graph('invalid_entry', starts);
  if (!pkg.nodes.some((node) => node.kind === 'end')) graph('missing_terminal');
  const outgoing = new Map<string, string[]>();
  const incoming = new Map<string, number>();
  for (const id of nodes.keys()) {
    outgoing.set(id, []);
    incoming.set(id, 0);
  }
  const seenEdges = new Set<string>();
  for (const edge of pkg.edges) {
    if (!nodes.has(edge.source) || !nodes.has(edge.target)) {
      graph(
        'unknown_edge_node',
        [edge.source, edge.target].filter((id) => nodes.has(id)),
      );
      continue;
    }
    const key = `${edge.source}\u0000${edge.target}`;
    if (seenEdges.has(key)) {
      graph('duplicate_edge', [edge.source, edge.target]);
      continue;
    }
    seenEdges.add(key);
    outgoing.get(edge.source)?.push(edge.target);
    incoming.set(edge.target, (incoming.get(edge.target) ?? 0) + 1);
  }
  if (entryValid && incoming.get(pkg.entry_node)) graph('invalid_entry', [pkg.entry_node]);
  for (const [id, node] of nodes) {
    const targets = outgoing.get(id) ?? [];
    if (node.kind === 'end' && targets.length) graph('terminal_outgoing', [id]);
    if (node.kind !== 'end' && !targets.length) graph('nonterminal_dead_end', [id]);
  }
  if (entryValid) {
    const reached = new Set<string>();
    const pending = [pkg.entry_node];
    while (pending.length) {
      const id = pending.pop() as string;
      if (reached.has(id)) continue;
      reached.add(id);
      pending.push(...(outgoing.get(id) ?? []));
    }
    const unreachable = [...nodes.keys()].filter((id) => !reached.has(id));
    if (unreachable.length) graph('unreachable_node', unreachable);
  }
  const remaining = new Map(incoming);
  const ready = [...remaining].filter(([, count]) => !count).map(([id]) => id);
  const visited = new Set<string>();
  while (ready.length) {
    const id = ready.shift() as string;
    visited.add(id);
    for (const target of outgoing.get(id) ?? []) {
      const count = (remaining.get(target) ?? 0) - 1;
      remaining.set(target, count);
      if (!count) ready.push(target);
    }
  }
  if (visited.size !== nodes.size)
    graph(
      'graph_cycle',
      [...nodes.keys()].filter((id) => !visited.has(id)),
    );
  return problems;
}

function sorted(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sorted);
  if (isRecord(value))
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, sorted(value[key])]),
    );
  return value;
}

// Sorted-key compact UTF-8 JSON with defaults, matching canonical_package_json. Array order
// stays significant. Call it only with a package that validateGraph accepted.
export function canonicalJson(pkg: WorkflowPackage): string {
  const complete: WorkflowPackage = {
    ...pkg,
    connectors: pkg.connectors.map((connector) => ({
      ...connector,
      secret_ref: connector.secret_ref ?? null,
    })),
  };
  return JSON.stringify(sorted(complete));
}

// Content identity only: the digest is neither a signature nor evidence of approval.
export async function packageDigest(canonical: string): Promise<string> {
  const hash = await crypto.subtle.digest('SHA-256', encoder.encode(canonical));
  return [...new Uint8Array(hash)].map((byte) => byte.toString(16).padStart(2, '0')).join('');
}

export function validatePackage(raw: unknown): Validation {
  const bounds = jsonBoundsProblem(raw);
  if (bounds) return { ok: false, problems: [bounds] };
  const read = readPackage(raw);
  if (!read.package) return { ok: false, problems: read.problems };
  const problems = validateGraph(read.package);
  if (problems.length) return { ok: false, problems };
  const canonical = canonicalJson(read.package);
  const complete = jsonBoundsProblem(JSON.parse(canonical));
  if (complete) return { ok: false, problems: [complete] };
  return { ok: true, package: read.package, canonical, problems: [] };
}
