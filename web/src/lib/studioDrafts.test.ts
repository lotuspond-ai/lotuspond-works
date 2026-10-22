import assert from 'node:assert/strict';
import test from 'node:test';
import { emptyDraft, moveNode, serializeDraft, updatePackage, parseDraft } from './studio.js';
import {
  MAX_DRAFTS,
  addDraft,
  canAddDraft,
  draftLabel,
  openDraft,
  readShelf,
  removeDraft,
  restoreDraft,
  serializeShelf,
  shelfKey,
  singleDraftKey,
  updateDraft,
  type DraftShelf,
} from './studioDrafts.js';

const ids = () => {
  let next = 0;
  return () => `d${(next += 1)}`;
};
const at = (minute: number) => `2026-10-22T10:${String(minute).padStart(2, '0')}:00.000Z`;
const named = (name: string) => updatePackage(emptyDraft(), { name });

test('drafts are kept per workspace and project, beside the earlier single-draft key', () => {
  assert.equal(shelfKey('w1', 'p1'), 'lotuspond.studio.drafts.w1.p1');
  assert.equal(shelfKey('w1', ''), 'lotuspond.studio.drafts.w1.workspace');
  assert.equal(singleDraftKey('w1', 'p1'), 'lotuspond.studio.w1.p1');
});

test('an empty or unreadable shelf opens as an empty list', () => {
  for (const stored of [null, '', 'not json', '[]', '{"drafts":"none"}'])
    assert.deepEqual(readShelf(stored, null, at(0), ids()), { drafts: [], open: null });
});

test('the earlier single draft moves onto the shelf and stays open', () => {
  const single = parseDraft(serializeDraft(named('Kept from before')));
  const shelf = readShelf(null, single, at(5), ids());
  assert.equal(shelf.drafts.length, 1);
  assert.equal(shelf.open, 'd1');
  assert.equal(draftLabel(shelf.drafts[0].draft), 'Kept from before');
});

test('a stored shelf survives a round trip and drops only unreadable entries', () => {
  let shelf: DraftShelf = { drafts: [], open: null };
  shelf = addDraft(shelf, named('First'), at(1), 'a');
  shelf = addDraft(shelf, named('Second'), at(2), 'b');
  const text = serializeShelf(shelf);
  assert.deepEqual(readShelf(text, null, at(9), ids()), shelf);

  const raw = JSON.parse(text);
  raw.drafts.push({ id: 'c', updatedAt: at(3), draft: { package: 'broken' } }, { id: 'a' });
  raw.open = 'missing';
  const read = readShelf(JSON.stringify(raw), null, at(9), ids());
  assert.deepEqual(
    read.drafts.map((entry) => entry.id),
    ['b', 'a'],
  );
  assert.equal(read.open, null);
});

test('the shelf lists the most recently changed draft first', () => {
  let shelf: DraftShelf = { drafts: [], open: null };
  shelf = addDraft(shelf, named('Older'), at(1), 'a');
  shelf = addDraft(shelf, named('Newer'), at(2), 'b');
  assert.deepEqual(
    shelf.drafts.map((entry) => entry.id),
    ['b', 'a'],
  );
  assert.equal(shelf.open, 'b');
  const moved = moveNode(shelf.drafts[1].draft, 'start', { x: 200, y: 200 });
  shelf = updateDraft(shelf, 'a', moved, at(3));
  assert.deepEqual(
    shelf.drafts.map((entry) => entry.id),
    ['a', 'b'],
  );
  assert.equal(shelf.drafts[0].updatedAt, at(3));
  // An unchanged draft or an unknown identifier changes nothing.
  assert.equal(updateDraft(shelf, 'a', shelf.drafts[0].draft, at(4)), shelf);
  assert.equal(updateDraft(shelf, 'zzz', moved, at(4)), shelf);
});

test('deleting closes the open draft and restoring puts it back in time order', () => {
  let shelf: DraftShelf = { drafts: [], open: null };
  shelf = addDraft(shelf, named('One'), at(1), 'a');
  shelf = addDraft(shelf, named('Two'), at(2), 'b');
  shelf = addDraft(shelf, named('Three'), at(3), 'c');
  const removed = shelf.drafts.find((entry) => entry.id === 'b')!;
  shelf = openDraft(shelf, 'b');
  shelf = removeDraft(shelf, 'b');
  assert.equal(shelf.open, null);
  shelf = restoreDraft(shelf, removed);
  assert.deepEqual(
    shelf.drafts.map((entry) => entry.id),
    ['c', 'b', 'a'],
  );
  assert.equal(restoreDraft(shelf, removed), shelf);
  assert.equal(openDraft(shelf, 'missing'), shelf);
});

test('the shelf stops accepting drafts at its bound', () => {
  let shelf: DraftShelf = { drafts: [], open: null };
  for (let index = 0; index < MAX_DRAFTS; index += 1)
    shelf = addDraft(shelf, emptyDraft(), at(index % 60), `d${index}`);
  assert.equal(canAddDraft(shelf), false);
  assert.equal(addDraft(shelf, emptyDraft(), at(59), 'extra'), shelf);
});

test('a draft is labelled by its name, then its identifier', () => {
  assert.equal(draftLabel(named('  Intake  ')), 'Intake');
  assert.equal(draftLabel(named('')), 'new-workflow');
  assert.equal(
    draftLabel(updatePackage(emptyDraft(), { name: '', package_id: '' })),
    'Untitled workflow',
  );
});
