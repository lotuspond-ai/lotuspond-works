import assert from 'node:assert/strict';
import test from 'node:test';
import { allPages, navigation } from '../components/console/navigation.js';
import { plannedArea, plannedAreas } from './roadmap.js';

const planned = new Set(plannedAreas.map((area) => area.id));

test('every planned area is a navigation item tagged Planned, and only those are', () => {
  const tagged = navigation.flatMap((section) =>
    section.items.filter((item) => item.planned).map((item) => item.id),
  );
  assert.deepEqual(tagged.sort(), [...planned].sort());
});

test('a planned page only points at what can be used today', () => {
  for (const area of plannedAreas)
    for (const item of area.today)
      assert.equal(plannedArea(item.href.slice(1)), undefined, `${area.id} → ${item.href}`);
});

test('a planned page only points at console pages that exist', () => {
  const pages = new Set(allPages.map((page) => page.id));
  for (const area of plannedAreas)
    for (const item of area.today)
      assert.ok(pages.has(item.href.slice(1)), `${area.id} → ${item.href}`);
});
