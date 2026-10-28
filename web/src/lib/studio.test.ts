import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { readPackage, validatePackage, type WorkflowPackage } from './packageContract.js';
import {
  GRID,
  NODE_WIDTH,
  addEdge,
  addNode,
  autoLayout,
  checkDraft,
  draftFromPackage,
  emptyDraft,
  emptyPackage,
  moveNode,
  nextNodeId,
  parseDraft,
  parseOperations,
  problemLocation,
  removeNode,
  renameNode,
  serializeDraft,
  updateNode,
} from './studio.js';

const examplePath = new URL('../../../contracts/examples/reviewed-action.v1.json', import.meta.url);
async function example(): Promise<WorkflowPackage> {
  const read = readPackage(JSON.parse(await readFile(examplePath, 'utf8')));
  assert.ok(read.package, 'the example must satisfy the contract');
  return read.package;
}

test('the blank draft passes the admission check and lays out left to right', () => {
  const draft = emptyDraft();
  const result = checkDraft(draft.package);
  assert.equal(result.ok, true);
  assert.ok(draft.layout.start.x < draft.layout.end.x);
  assert.equal(draft.layout.start.y, draft.layout.end.y);
  for (const position of Object.values(draft.layout)) {
    assert.equal(position.x % GRID, 0);
    assert.equal(position.y % GRID, 0);
  }
});

test('the layout places each node one column past the node it follows', async () => {
  const pkg = await example();
  const layout = autoLayout(pkg);
  const order = ['start', 'read', 'propose', 'review', 'write', 'end'];
  for (let index = 1; index < order.length; index += 1)
    assert.ok(layout[order[index]].x > layout[order[index - 1]].x + NODE_WIDTH);
  // A node the entry cannot reach takes the column after the deepest reachable one.
  const stranded = {
    ...pkg,
    nodes: [...pkg.nodes, { id: 'orphan', kind: 'human_review' as const }],
  };
  assert.ok(autoLayout(stranded).orphan.x > layout.end.x);
});

test('new node identifiers never collide and follow the kind', () => {
  const pkg = emptyPackage();
  assert.equal(nextNodeId(pkg, 'agent'), 'agent');
  assert.equal(nextNodeId(pkg, 'human_review'), 'human-review');
  assert.equal(nextNodeId(pkg, 'start'), 'start-2');
  let draft = addNode(emptyDraft(), 'agent');
  draft = addNode(draft, 'agent');
  assert.deepEqual(
    draft.package.nodes.map((node) => node.id),
    ['start', 'end', 'agent', 'agent-2'],
  );
  assert.ok(draft.layout['agent-2'], 'the added node has a position');
});

test('adding a node at a pointer position snaps it to the grid', () => {
  const draft = addNode(emptyDraft(), 'connector', { x: 101, y: 59 });
  assert.deepEqual(draft.layout.connector, { x: 104, y: 56 });
  assert.deepEqual(moveNode(draft, 'connector', { x: -30, y: 13 }).layout.connector, {
    x: 0,
    y: 16,
  });
});

test('renaming a node carries its edges, entry and position along', () => {
  const draft = renameNode(emptyDraft(), 'start', 'begin');
  assert.equal(draft.package.entry_node, 'begin');
  assert.deepEqual(draft.package.edges, [{ source: 'begin', target: 'end' }]);
  assert.ok(draft.layout.begin);
  assert.equal(draft.layout.start, undefined);
  // A collision or a no-op rename leaves the draft untouched.
  assert.equal(renameNode(draft, 'begin', 'end'), draft);
  assert.equal(renameNode(draft, 'begin', 'begin'), draft);
});

test('edges refuse self-loops and repeats; removing a node removes its edges', () => {
  let draft = addNode(emptyDraft(), 'human_review');
  draft = addEdge(draft, { source: 'start', target: 'human-review' });
  draft = addEdge(draft, { source: 'start', target: 'human-review' });
  draft = addEdge(draft, { source: 'human-review', target: 'human-review' });
  assert.equal(draft.package.edges.length, 2);
  draft = removeNode(draft, 'human-review');
  assert.deepEqual(draft.package.edges, [{ source: 'start', target: 'end' }]);
  assert.equal(draft.layout['human-review'], undefined);
});

test('the entry node follows the single start node', () => {
  let draft = removeNode(emptyDraft(), 'start');
  assert.equal(draft.package.entry_node, 'start');
  draft = addNode(draft, 'start');
  assert.equal(draft.package.entry_node, 'start');
  draft = updateNode(draft, 'start', { id: 'start', kind: 'start' });
  const second = addNode(draft, 'start');
  // Two starts are left for the admission check to report.
  assert.equal(second.package.entry_node, 'start');
  assert.equal(checkDraft(second.package).ok, false);
});

test('a cleared limit is reported as that field, not as unreadable JSON', () => {
  let draft = addNode(emptyDraft(), 'agent');
  const agent = draft.package.nodes.find((node) => node.kind === 'agent');
  assert.ok(agent && agent.kind === 'agent');
  draft = updateNode(draft, 'agent', {
    ...agent,
    limits: { ...agent.limits, max_steps: Number.NaN },
  });
  const result = checkDraft(draft.package);
  assert.equal(result.ok, false);
  assert.ok(!result.ok);
  const problem = result.problems.find((item) => item.path.at(-1) === 'max_steps');
  assert.ok(problem, 'the cleared limit is reported');
  assert.deepEqual(problem.path, ['nodes', 2, 'agent', 'limits', 'max_steps']);
  assert.equal(problem.code, 'int_type');
  assert.ok(result.problems.every((item) => item.code !== 'nonfinite_number'));
});

test('stored drafts round-trip, and a draft in progress survives a reload', async () => {
  const pkg = await example();
  const draft = draftFromPackage(pkg);
  const restored = parseDraft(serializeDraft(draft));
  assert.deepEqual(restored, draft);
  // An in-progress draft fails the contract (an empty identifier) but is still restored.
  const inProgress = { ...draft, package: { ...pkg, package_id: '' } };
  const loose = parseDraft(serializeDraft(inProgress));
  assert.ok(loose);
  assert.equal(loose.package.package_id, '');
  assert.equal(loose.package.nodes.length, pkg.nodes.length);
  assert.equal(validatePackage(loose.package).ok, false);
  // Garbage, an empty record and a draft without nodes are discarded.
  assert.equal(parseDraft('not json'), null);
  assert.equal(parseDraft('{}'), null);
  assert.equal(parseDraft(JSON.stringify({ package: { nodes: [] }, layout: {} })), null);
  // A corrupt position falls back to the margin rather than breaking the canvas.
  const corrupt = parseDraft(
    JSON.stringify({ package: pkg, layout: { start: { x: 'wide', y: Number.POSITIVE_INFINITY } } }),
  );
  assert.ok(corrupt);
  assert.ok(Number.isFinite(corrupt.layout.start.x));
});

test('operations are read from a comma or space separated list', () => {
  assert.deepEqual(parseOperations(' read-case, write-case  send'), [
    'read-case',
    'write-case',
    'send',
  ]);
  assert.deepEqual(parseOperations(''), []);
});

test('problem locations read as a path, the nodes concerned, or the package', () => {
  assert.equal(
    problemLocation({ path: ['nodes', 1, 'agent', 'model_ref'], code: '', message: '', nodes: [] }),
    'nodes › 1 › agent › model ref',
  );
  assert.equal(problemLocation({ path: [], code: '', message: '', nodes: ['a', 'b'] }), 'a, b');
  assert.equal(problemLocation({ path: [], code: '', message: '', nodes: [] }), 'Package');
});
