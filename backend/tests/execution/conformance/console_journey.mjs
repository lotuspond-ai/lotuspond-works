// A reference workflow decided in the real console (test-only).
//
// `node console_journey.mjs PLAN.json` drives the built console in Chromium as the people of
// one workspace, each with their own session. PLAN.runs lists executions already started;
// each names its tasks in order. For each task its person opens Human tasks, finds the
// execution's open review or form, claims it, and allows the review or submits the form.
// A task with `refused_first` is first claimed by that person, whose Allow must be refused
// for independent approval and who releases it. Control Room then shows each execution
// and its writes, and is checked at 768 and 375 px too. Screenshots go to PLAN.shots; what
// each person saw is written to PLAN.result as `{ ok, runs: { name: {...} }, focus }`.
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { join } from 'node:path';

// Resolved as CommonJS, so a Playwright installed globally (NODE_PATH) serves as well as
// the repository's own; an ES import would see only the latter.
const { chromium } = createRequire(import.meta.url)('playwright');

const plan = JSON.parse(readFileSync(process.argv[2], 'utf8'));
mkdirSync(plan.shots, { recursive: true });
const result = { runs: {}, focus: [], errors: [], failures: [] };
const WIDTHS = { desktop: [1440, 900], tablet: [768, 1024], phone: [375, 812] };

const browser = await chromium.launch();
const pages = []; // every person's page, shown on failure

async function person(who, width = 'desktop') {
  const [w, h] = WIDTHS[width];
  const context = await browser.newContext({ viewport: { width: w, height: h } });
  const { hostname } = new URL(plan.url);
  await context.addCookies([
    { name: 'lotuspond_session', value: plan.people[who], domain: hostname, path: '/' },
  ]);
  const page = await context.newPage();
  pages.push([who, page]);
  page.on('pageerror', (error) => result.errors.push({ who, message: String(error) }));
  return { context, page };
}

async function shot(page, name) {
  await page.screenshot({ path: join(plan.shots, `${name}.png`), fullPage: true });
}

// A person opens the page, switches to the organization workspace and picks the project
// in the top bar, as anyone with a personal workspace too would.
async function visit(page, hash) {
  // A hash-only goto stays in the same document; reload so every visit reads afresh.
  const loaded = page.url().startsWith(plan.url);
  await page.goto(`${plan.url}/#${hash}`);
  if (loaded) await page.reload();
  // The breadcrumb names the current workspace at every width.
  const current = page.locator('.crumbs > span').first();
  await current.waitFor();
  if ((await current.innerText()).trim() !== plan.workspace) {
    const menu = page.getByRole('button', { name: 'Menu', exact: true });
    const narrow = await menu.isVisible().catch(() => false);
    if (narrow) await menu.click(); // the sidebar is behind a menu on narrow screens
    await page.getByRole('button', { name: /Switch workspace/ }).click();
    await page.locator('.switch-option', { hasText: plan.workspace }).click();
    await page.locator('.crumbs > span', { hasText: plan.workspace }).first().waitFor();
    const close = page.getByRole('button', { name: 'Close', exact: true });
    if (narrow && (await close.isVisible().catch(() => false))) await close.click();
  }
  const project = page.locator('.project-filter select'); // labelled "Project" in the top bar
  await project.locator('option', { hasText: plan.project }).waitFor({ state: 'attached' });
  await project.selectOption({ label: plan.project });
}

const TITLES = { review: 'Review an action', form: 'Complete a form' };

// The execution's open task of this kind: rows are opened in turn until the detail names
// the execution. The inbox lists a task once its wait is retained, so it is refreshed
// until one appears.
async function openTask(page, executionId, kind, seconds = 240) {
  const deadline = Date.now() + seconds * 1000;
  await visit(page, 'human-tasks');
  for (;;) {
    const rows = page.getByRole('button', { name: new RegExp(TITLES[kind]) });
    const count = await rows.count();
    for (let index = 0; index < count; index += 1) {
      await rows.nth(index).click();
      const facts = page.locator('.operate-detail .record-facts');
      await facts.waitFor();
      const named = facts.locator('dt:has-text("Execution") + dd');
      if ((await named.innerText()).replace(/\s+/g, '') === executionId) {
        if (kind === 'review')
          await page.getByRole('heading', { name: 'The proposed action' }).waitFor();
        return;
      }
    }
    if (Date.now() > deadline) {
      await shot(page, `failure-inbox-${executionId.slice(0, 8)}`);
      const text = (await page.locator('body').innerText()).replace(/\s+/g, ' ').slice(0, 1500);
      throw new Error(`no ${kind} for ${executionId} appeared; the page said: ${text}`);
    }
    await page.waitForTimeout(1500);
    await page
      .getByRole('button', { name: 'Refresh' })
      .first()
      .click()
      .catch(() => {});
  }
}

async function proposed(page) {
  const section = page.locator('.operate-detail');
  return section.locator('h3:has-text("The proposed action") + p strong').first().innerText();
}

async function claim(page) {
  await page.getByRole('button', { name: 'Claim', exact: true }).click();
  await page.getByText('Claimed. It is yours to decide.').waitFor();
}

// Keyboard: the claim control is reachable from the task heading and shows a focus ring.
async function focusClaim(page, name) {
  await page.locator('#task-heading').focus();
  for (let i = 0; i < 40; i += 1) {
    await page.keyboard.press('Tab');
    const label = await page.evaluate(() => document.activeElement?.textContent?.trim());
    if (label === 'Claim') break;
  }
  result.focus.push(
    await page.evaluate(() => {
      const element = document.activeElement;
      const style = element ? getComputedStyle(element) : null;
      return {
        label: element?.textContent?.trim(),
        outline: style ? `${style.outlineStyle} ${style.outlineWidth}` : null,
      };
    }),
  );
  await shot(page, `${name}-claim-focus`);
}

const people = {};
async function personFor(who, width = 'desktop') {
  const key = `${who}:${width}`;
  if (!people[key]) people[key] = await person(who, width);
  return people[key].page;
}

async function doTask(run, task, index) {
  const name = `${run.name}-${index + 1}`;
  const seen = { who: task.who, kind: task.kind };
  if (task.refused_first) {
    // The requester may not allow an action that needs independent approval.
    const page = await personFor(task.refused_first);
    await openTask(page, run.execution_id, 'review');
    seen.refused_operation = await proposed(page);
    seen.independent = await page.getByText('This action needs independent approval').isVisible();
    await claim(page);
    await page.getByRole('button', { name: 'Allow this action' }).click();
    const refusal = page.locator('.operate-detail [role="alert"]');
    await refusal.waitFor({ timeout: 30000 });
    seen.refusal = (await refusal.innerText()).trim();
    await shot(page, `${name}-refused`);
    await page.getByRole('button', { name: 'Release' }).click();
    await page.getByText('Released for someone else.').waitFor();
  }
  const page = await personFor(task.who, task.width ?? 'desktop');
  await openTask(page, run.execution_id, task.kind);
  if (task.kind === 'review') seen.operation = await proposed(page);
  if (task.focus) await focusClaim(page, name);
  if (task.kind === 'form') {
    // What the form asks, as it is shown before anyone claims it.
    await page.getByRole('heading', { name: 'What this form asks' }).waitFor();
    seen.asks = (await page.locator('.form-asks-list').innerText()).replace(/\s+/g, ' ').trim();
  }
  await shot(page, `${name}-${task.kind}-${task.width ?? 'desktop'}`);
  await claim(page);
  if (task.kind === 'review') {
    const allow = page.getByRole('button', { name: 'Allow this action' });
    await allow.waitFor();
    await allow.click();
    await page.getByText('Allowed. The action can now be sent.').waitFor({ timeout: 30000 });
    seen.allowed = true;
  } else {
    // Each field by its label (the field name, as the console labels it).
    for (const [field, value] of Object.entries(task.payload)) {
      const label = field.replaceAll('_', ' ').replace(/^./, (letter) => letter.toUpperCase());
      await page.getByLabel(label, { exact: true }).fill(String(value));
    }
    await shot(page, `${name}-${task.kind}-filled-${task.width ?? 'desktop'}`);
    await page.getByRole('button', { name: 'Submit', exact: true }).click();
    await page.getByText('Submitted.').waitFor({ timeout: 30000 });
    seen.submitted = true;
  }
  return seen;
}

// Control Room: the execution's state and each write, once every write it expects is
// verified (or the deadline passes, and the test reads what was shown).
async function controlRoom(page, run, seconds = 420) {
  const deadline = Date.now() + seconds * 1000;
  let shown = {};
  for (;;) {
    await visit(page, 'control-room');
    await page.getByRole('tab', { name: 'Runs' }).click();
    await page.getByTitle(run.execution_id).click();
    await page.getByRole('heading', { name: 'Writes' }).waitFor();
    await page.waitForTimeout(500);
    const detail = page.locator('.operate-detail');
    shown = {
      state: (await detail.locator('dt:has-text("Observed state") + dd').innerText()).trim(),
      writes: await page
        .locator('.operate-trace li')
        .evaluateAll((items) => items.map((item) => item.innerText.replace(/\s+/g, ' ').trim())),
    };
    const settled =
      /succeeded/i.test(shown.state) &&
      shown.writes.length === run.writes &&
      shown.writes.every((text) => /Outcome: verified/i.test(text));
    if (settled || Date.now() > deadline) return shown;
    await page.waitForTimeout(2000);
  }
}

try {
  for (const run of plan.runs) {
    const seen = { tasks: [] };
    result.runs[run.name] = seen;
    for (const [index, task] of run.tasks.entries())
      seen.tasks.push(await doTask(run, task, index));
  }
  const viewer = await personFor('requester');
  for (const run of plan.runs) {
    result.runs[run.name].control_room = await controlRoom(viewer, run);
    await shot(viewer, `${run.name}-control-room-desktop`);
  }
  // The last run's execution at narrower widths: no horizontal scroll.
  const last = plan.runs[plan.runs.length - 1];
  for (const width of ['tablet', 'phone']) {
    const page = await personFor('requester', width);
    await visit(page, 'control-room');
    await page.getByRole('tab', { name: 'Runs' }).click();
    await page.getByTitle(last.execution_id).click();
    await page.getByRole('heading', { name: 'Writes' }).waitFor();
    await page.waitForTimeout(500);
    result.runs[last.name][`overflow_${width}`] = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    await shot(page, `${last.name}-control-room-${width}`);
  }
  result.ok = true;
} catch (error) {
  result.ok = false;
  result.error = String(error?.message ?? error).slice(0, 600);
  for (const [index, [who, page]] of pages.entries()) {
    await shot(page, `failure-${index}-${who}`).catch(() => {});
    result.failures.push({
      who,
      url: page.url(),
      text: (
        await page
          .locator('body')
          .innerText()
          .catch(() => '')
      )
        .replace(/\s+/g, ' ')
        .slice(0, 800),
    });
  }
} finally {
  writeFileSync(plan.result, JSON.stringify(result, null, 2));
  await browser.close();
}
