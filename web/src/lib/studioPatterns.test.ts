import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import test from 'node:test';
import { packageDigest, validatePackage } from './packageContract.js';
import { emptyDraft } from './studio.js';
import { patternDraft, patternPackage, patternSteps, patterns } from './studioPatterns.js';

const examples = new URL('../../../contracts/examples/', import.meta.url);
// Recorded with `npm run package:validate -- contracts/examples/<id>.v1.json`, so the browser
// port and the shared SDK validator agree on every pattern's canonical bytes.
const CLI_DIGESTS: Record<string, string> = {
  'case-approval': '6db6cddee90bb75817ff7def82675b2d27c2a37448ae2a8a8507f20c5d789a48',
  'document-intake': 'fa0e5f98966db9036cdd22b577264fba0c786905bfb8179502faac3ae5528489',
  'knowledge-answer': '65a1a29c7508805b346dfe81e8556dbee5fe8f197c7d083c4cc98229a9e2f720',
  'record-reconciliation': 'aa4bacc97e6a5472be6aad056b615bec997bc6dcbac4e2333fc96c0aa83b738c',
  'reviewed-action': '305efc96011ba5fc2f527cefbe416890b0265269e18e3402354ffbfed40251d3',
};

test('every example package in the contract is offered as a pattern, and nothing else', async () => {
  const packages: string[] = [];
  for (const name of await readdir(examples)) {
    if (!name.endsWith('.v1.json')) continue;
    const raw = JSON.parse(await readFile(new URL(name, examples), 'utf8'));
    if (raw.schema_version === 'lotuspond.workflow-package.v1') packages.push(raw.package_id);
  }
  assert.deepEqual(patterns.map((pattern) => pattern.id).sort(), packages.sort());
});

test('each pattern passes the admission check with the CLI digest', async () => {
  for (const pattern of patterns) {
    const result = validatePackage(pattern.source);
    assert.equal(result.ok, true, `${pattern.id} must satisfy the contract`);
    if (!result.ok) continue;
    assert.equal(result.package.package_id, pattern.id);
    assert.ok(result.package.name.startsWith('Synthetic'), `${pattern.id} is labelled synthetic`);
    for (const connector of result.package.connectors)
      assert.ok(connector.connector.startsWith('synthetic-'), `${pattern.id} uses placeholders`);
    assert.equal(await packageDigest(result.canonical), CLI_DIGESTS[pattern.id]);
  }
});

test('a pattern opens as a laid-out draft, and an unknown one as the blank draft', () => {
  const draft = patternDraft('document-intake');
  assert.equal(draft.package.package_id, 'document-intake');
  assert.deepEqual(Object.keys(draft.layout).sort(), draft.package.nodes.map((n) => n.id).sort());
  assert.deepEqual(patternDraft('no-such-pattern'), emptyDraft());
});

test('the chooser names the steps a pattern passes through', () => {
  const steps = (id: string) => {
    const pkg = patternPackage(patterns.find((pattern) => pattern.id === id)!);
    assert.ok(pkg);
    return patternSteps(pkg);
  };
  assert.equal(steps('reviewed-action'), '2 connectors · 1 agent · 1 human review');
  assert.equal(steps('document-intake'), '2 connectors · 2 agents · 1 human review');
  assert.equal(steps('case-approval'), '3 connectors · 1 agent · 1 human review');
});
