import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import {
  BOUNDS,
  ERROR_MESSAGES,
  IDENTIFIER,
  LIMIT_RANGES,
  MAX_JSON_DEPTH,
  MAX_JSON_ITEMS,
  MAX_PACKAGE_BYTES,
  REFERENCE_REVISION,
  SCHEMA_VERSION,
  VERSION,
  canonicalJson,
  packageDigest,
  readPackage,
  validateGraph,
  validatePackage,
  type WorkflowPackage,
} from './packageContract.js';

const examplePath = new URL('../../../contracts/examples/reviewed-action.v1.json', import.meta.url);
const sourcePath = new URL('../../../backend/lotuspond/package_contract.py', import.meta.url);
// Recorded with `npm run package:validate -- contracts/examples/reviewed-action.v1.json`.
const EXAMPLE_DIGEST = '305efc96011ba5fc2f527cefbe416890b0265269e18e3402354ffbfed40251d3';

type Raw = Record<string, any>;
async function example(): Promise<Raw> {
  return JSON.parse(await readFile(examplePath, 'utf8'));
}
function firstCode(raw: unknown): string {
  const result = validatePackage(raw);
  assert.equal(result.ok, false, 'expected the package to be rejected');
  return result.ok ? '' : result.problems[0].code;
}

test('every message, bound and pattern is quoted from the server validator', async () => {
  const source = await readFile(sourcePath, 'utf8');
  for (const [code, message] of Object.entries(ERROR_MESSAGES))
    assert.ok(source.includes(`"${code}": "${message}"`), code);
  assert.ok(source.includes(`SCHEMA_VERSION = "${SCHEMA_VERSION}"`));
  assert.equal(MAX_PACKAGE_BYTES, 1024 * 1024);
  assert.ok(source.includes(`MAX_JSON_DEPTH = ${MAX_JSON_DEPTH}`));
  assert.ok(source.includes(`MAX_JSON_ITEMS = ${MAX_JSON_ITEMS}`));
  for (const pattern of [IDENTIFIER, VERSION, REFERENCE_REVISION])
    assert.ok(source.includes(`r"${pattern.source}"`), pattern.source);
  assert.ok(
    source.includes(`min_length=${BOUNDS.identifier.min}, max_length=${BOUNDS.identifier.max}`),
  );
  assert.ok(
    source.includes(`min_length=${BOUNDS.instructions.min}, max_length=${BOUNDS.instructions.max}`),
  );
  assert.ok(source.includes(`min_length=${BOUNDS.nodes.min}, max_length=${BOUNDS.nodes.max}`));
  assert.ok(source.includes(`min_length=${BOUNDS.edges.min}, max_length=${BOUNDS.edges.max}`));
  for (const [field, range] of Object.entries(LIMIT_RANGES))
    assert.ok(source.includes(`${field}: int = Field(ge=${range.min}, le=${range.max})`), field);
});

test('the reviewed-action example validates and reproduces the recorded digest', async () => {
  const result = validatePackage(await example());
  assert.ok(result.ok);
  assert.equal(result.package.package_id, 'reviewed-action');
  assert.equal(await packageDigest(result.canonical), EXAMPLE_DIGEST);
  assert.ok(result.canonical.includes('"secret_ref":"case-credential"'));
});

test('the digest ignores key order and defaults but changes with content', async () => {
  const raw = await example();
  const reordered = Object.fromEntries(Object.entries(raw).reverse());
  reordered.nodes = raw.nodes.map((node: Raw) =>
    Object.fromEntries(Object.entries(node).reverse()),
  );
  delete reordered.connectors[0].secret_ref;
  reordered.secret_refs = [];
  const explicit = await example();
  explicit.connectors[0].secret_ref = null;
  explicit.secret_refs = [];
  const first = validatePackage(reordered);
  const second = validatePackage(explicit);
  assert.ok(first.ok && second.ok);
  assert.equal(first.canonical, second.canonical);
  assert.ok(first.canonical.includes('"secret_ref":null'));
  const changed = await example();
  changed.nodes[2].instructions = 'Propose a different update.';
  const third = validatePackage(changed);
  assert.ok(third.ok);
  assert.notEqual(await packageDigest(third.canonical), EXAMPLE_DIGEST);
  assert.match(await packageDigest(third.canonical), /^[0-9a-f]{64}$/);
});

test('credentials and unknown fields are rejected at every boundary', async () => {
  for (const target of ['package', 'connector', 'model', 'secret', 'node', 'limits', 'edge']) {
    const raw = await example();
    const location: Raw = {
      package: raw,
      connector: raw.connectors[0],
      model: raw.models[0],
      secret: raw.secret_refs[0],
      node: raw.nodes[2],
      limits: raw.nodes[2].limits,
      edge: raw.edges[0],
    }[target];
    location.api_key = 'must-never-be-an-embedded-credential';
    const result = validatePackage(raw);
    assert.ok(!result.ok, target);
    assert.equal(result.problems[0].code, 'extra_forbidden', target);
    assert.equal(result.problems[0].path.at(-1), 'api_key', target);
  }
});

test('agent limits are required, strict, positive and bounded', async () => {
  for (const field of Object.keys(LIMIT_RANGES)) {
    for (const [value, code] of [
      [undefined, 'missing'],
      [0, 'greater_than_equal'],
      [-1, 'greater_than_equal'],
      [true, 'int_type'],
      ['5', 'int_type'],
      [10 ** 12, 'less_than_equal'],
    ] as const) {
      const raw = await example();
      if (value === undefined) delete raw.nodes[2].limits[field];
      else raw.nodes[2].limits[field] = value;
      const result = validatePackage(raw);
      assert.ok(!result.ok, `${field}=${String(value)}`);
      assert.equal(result.problems[0].code, code, `${field}=${String(value)}`);
      assert.deepEqual(result.problems[0].path, ['nodes', 2, 'agent', 'limits', field]);
      assert.deepEqual(result.problems[0].nodes, ['propose']);
    }
  }
});

test('unsupported schema, node kind and version are rejected', async () => {
  const schema = await example();
  schema.schema_version = 'lotuspond.workflow-package.v99';
  assert.equal(firstCode(schema), 'literal_error');
  const kind = await example();
  kind.nodes[2].kind = 'python-exec';
  assert.equal(firstCode(kind), 'union_tag_invalid');
  const version = await example();
  version.version = 'latest';
  assert.equal(firstCode(version), 'string_pattern_mismatch');
});

test('declared identifiers must be unique', async () => {
  for (const collection of ['models', 'connectors', 'secret_refs', 'nodes']) {
    const raw = await example();
    raw[collection].push(structuredClone(raw[collection][0]));
    assert.equal(firstCode(raw), 'duplicate_identifier', collection);
  }
});

test('references, operations, edges and the entry must be declared', async () => {
  const cases: [string, Raw, string, string][] = [];
  for (const [target, code] of [
    ['model', 'unknown_model_reference'],
    ['connector', 'unknown_connector_reference'],
    ['secret', 'unknown_secret_reference'],
    ['operation', 'unknown_operation'],
    ['edge', 'unknown_edge_node'],
    ['entry', 'invalid_entry'],
  ] as const) {
    const raw = await example();
    const [location, field]: [Raw, string] = {
      model: [raw.nodes[2], 'model_ref'],
      connector: [raw.nodes[1], 'connector_ref'],
      secret: [raw.connectors[0], 'secret_ref'],
      operation: [raw.nodes[1], 'operation'],
      edge: [raw.edges[0], 'target'],
      entry: [raw, 'entry_node'],
    }[target] as [Raw, string];
    location[field] = 'undeclared';
    cases.push([target, raw, field, code]);
  }
  for (const [target, raw, , code] of cases) assert.equal(firstCode(raw), code, target);
});

test('the graph rejects cycles, unreachable nodes, dead ends and misplaced edges', async () => {
  for (const [defect, code, marked] of [
    ['cycle', 'graph_cycle', ['propose', 'review', 'write', 'end']],
    ['unreachable', 'unreachable_node', ['orphan']],
    ['dead-end', 'nonterminal_dead_end', ['write']],
    ['end-outgoing', 'terminal_outgoing', ['end']],
    ['entry-incoming', 'invalid_entry', ['start']],
  ] as const) {
    const raw = await example();
    if (defect === 'cycle') raw.edges.push({ source: 'write', target: 'propose' });
    else if (defect === 'unreachable') raw.nodes.push({ id: 'orphan', kind: 'end' });
    else if (defect === 'dead-end') raw.edges = raw.edges.slice(0, -1);
    else if (defect === 'end-outgoing') raw.edges.push({ source: 'end', target: 'read' });
    else raw.edges.push({ source: 'write', target: 'start' });
    const result = validatePackage(raw);
    assert.ok(!result.ok, defect);
    assert.equal(result.problems[0].code, code, defect);
    assert.deepEqual(result.problems[0].nodes, marked, defect);
  }
});

test('the graph requires one start matching the entry and an end', async () => {
  for (const [defect, code] of [
    ['wrong-entry', 'invalid_entry'],
    ['second-start', 'invalid_entry'],
    ['no-end', 'missing_terminal'],
  ] as const) {
    const raw = await example();
    if (defect === 'wrong-entry') raw.entry_node = 'read';
    else if (defect === 'second-start') raw.nodes[1] = { id: 'read', kind: 'start' };
    else raw.nodes[raw.nodes.length - 1] = { id: 'end', kind: 'human_review' };
    assert.equal(firstCode(raw), code, defect);
  }
});

test('duplicate edges and connector operations are rejected', async () => {
  const edge = await example();
  edge.edges.push(structuredClone(edge.edges[0]));
  assert.equal(firstCode(edge), 'duplicate_edge');
  const operation = await example();
  operation.connectors[0].operations.push('read-case');
  assert.equal(firstCode(operation), 'duplicate_operation');
});

test('a reference-only package without agents is supported', async () => {
  const raw = await example();
  raw.models = [];
  raw.nodes[2] = { id: 'propose', kind: 'human_review' };
  const result = validatePackage(raw);
  assert.ok(result.ok);
  assert.equal(result.package.models.length, 0);
});

test('deep, oversized, nonfinite, cyclic and malformed inputs are rejected', async () => {
  const depth = await example();
  let nested: unknown[] = [];
  for (let index = 0; index < 40; index += 1) nested = [nested];
  depth.extra = nested;
  assert.equal(firstCode(depth), 'input_too_deep');
  const bytes = await example();
  bytes.name = 'x'.repeat(MAX_PACKAGE_BYTES + 1);
  assert.equal(firstCode(bytes), 'input_too_large');
  const nonfinite = await example();
  nonfinite.nodes[2].limits.max_steps = Number.NaN;
  assert.equal(firstCode(nonfinite), 'nonfinite_number');
  const cyclic = await example();
  cyclic.extra = cyclic;
  assert.equal(firstCode(cyclic), 'cyclic_input');
  const surrogate = await example();
  surrogate.name = 'broken \uD83D text';
  assert.equal(firstCode(surrogate), 'invalid_unicode');
  const functionValue = await example();
  functionValue.extra = () => undefined;
  assert.equal(firstCode(functionValue), 'invalid_json_value');
  assert.equal(firstCode('not an object'), 'model_type');
  assert.equal(firstCode([]), 'model_type');
});

test('every graph problem is collected with the nodes it concerns', async () => {
  const raw = await example();
  raw.edges = raw.edges.slice(0, -1);
  raw.nodes.push({ id: 'orphan', kind: 'human_review' });
  const read = readPackage(raw);
  assert.ok(read.package);
  const problems = validateGraph(read.package);
  assert.deepEqual(
    problems.map((problem) => [problem.code, problem.nodes]),
    [
      ['nonterminal_dead_end', ['write']],
      ['nonterminal_dead_end', ['orphan']],
      ['unreachable_node', ['end', 'orphan']],
    ],
  );
});

test('canonical JSON sorts keys recursively and keeps array order', async () => {
  const read = readPackage(await example());
  assert.ok(read.package);
  const pkg: WorkflowPackage = read.package;
  const canonical = canonicalJson(pkg);
  assert.ok(canonical.startsWith('{"connectors":[{"connector":"synthetic-case-system"'));
  assert.ok(canonical.indexOf('"edges"') < canonical.indexOf('"entry_node"'));
  assert.ok(canonical.includes('"nodes":[{"id":"start","kind":"start"},{"connector_ref"'));
  assert.equal(canonical, JSON.stringify(JSON.parse(canonical)), 'compact separators');
});
