// Late purchase order recovery, decided in the real console (test-only).
//
// `node console_journey.mjs PLAN.json` drives the built console in Chromium as two
// people of one workspace, each with their own session: the requester allows the ERP
// date change in Human tasks; the customer update needs independent approval, so the
// requester's Allow is refused and released, and a second planner allows it. Control
// Room then shows the execution's two writes. Screenshots go to PLAN.shots at 1440,
// 768 and 375 px; the result (what each person saw) is written to PLAN.result.
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { join } from 'node:path';

// Resolved as CommonJS, so a Playwright installed globally (NODE_PATH) serves as well as
// the repository's own; an ES import would see only the latter.
const { chromium } = createRequire(import.meta.url)('playwright');

const plan = JSON.parse(readFileSync(process.argv[2], 'utf8'));
mkdirSync(plan.shots, { recursive: true });
const result = { steps: [], focus: [] };
const step = (name, detail = {}) => result.steps.push({ name, ...detail });
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
  page.on('pageerror', (error) => step('page error', { who, message: String(error) }));
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

// The inbox lists a review once its wait is retained; reload until one appears.
async function openReview(page, seconds = 240) {
  const deadline = Date.now() + seconds * 1000;
  await visit(page, 'human-tasks');
  for (;;) {
    const row = page.getByRole('button', { name: /Review an action/ }).first();
    if (await row.isVisible().catch(() => false)) {
      await row.click();
      await page.getByRole('heading', { name: 'The proposed action' }).waitFor();
      return;
    }
    if (Date.now() > deadline) {
      await shot(page, 'failure-inbox');
      const text = (await page.locator('body').innerText()).replace(/\s+/g, ' ').slice(0, 1500);
      throw new Error(`no review appeared in the inbox; the page said: ${text}`);
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

async function decide(page, notice) {
  await page.getByRole('button', { name: 'Claim', exact: true }).click();
  await page.getByText('Claimed. It is yours to decide.').waitFor();
  const allow = page.getByRole('button', { name: 'Allow this action' });
  await allow.waitFor();
  await allow.click();
  await page.getByText(notice).waitFor({ timeout: 30000 });
}

try {
  // 1. The requester reviews the exact date change and allows it.
  const requester = await person('requester');
  await openReview(requester.page);
  const first = await proposed(requester.page);
  step('requester opened the review', { operation: first });
  await shot(requester.page, 'review-desktop');
  // Keyboard: the claim control is reachable and shows a focus ring.
  await requester.page.locator('#task-heading').focus();
  for (let i = 0; i < 40; i += 1) {
    await requester.page.keyboard.press('Tab');
    const label = await requester.page.evaluate(() => document.activeElement?.textContent?.trim());
    if (label === 'Claim') break;
  }
  const focused = await requester.page.evaluate(() => {
    const element = document.activeElement;
    const style = element ? getComputedStyle(element) : null;
    return {
      label: element?.textContent?.trim(),
      outline: style ? `${style.outlineStyle} ${style.outlineWidth}` : null,
      boxShadow: style?.boxShadow ?? null,
    };
  });
  result.focus.push(focused);
  await shot(requester.page, 'review-claim-focus');
  await decide(requester.page, 'Allowed. The action can now be sent.');
  step('requester allowed', { operation: first });

  // 2. The customer update: the requester may not allow it.
  await openReview(requester.page);
  const second = await proposed(requester.page);
  const independent = await requester.page
    .getByText('This action needs independent approval')
    .isVisible();
  step('requester opened the second review', { operation: second, independent });
  await requester.page.getByRole('button', { name: 'Claim', exact: true }).click();
  await requester.page.getByText('Claimed. It is yours to decide.').waitFor();
  await requester.page.getByRole('button', { name: 'Allow this action' }).click();
  const refusal = requester.page.locator('.operate-detail [role="alert"]');
  await refusal.waitFor({ timeout: 30000 });
  step('requester was refused', { message: (await refusal.innerText()).trim() });
  await shot(requester.page, 'independent-refused');
  await requester.page.getByRole('button', { name: 'Release' }).click();
  await requester.page.getByText('Released for someone else.').waitFor();

  // 3. A second planner allows it, on a phone-width screen.
  const planner = await person('second', 'phone');
  await openReview(planner.page);
  step('second planner opened the review', { operation: await proposed(planner.page) });
  await shot(planner.page, 'review-phone');
  await decide(planner.page, 'Allowed. The action can now be sent.');
  step('second planner allowed');

  // 4. Control Room: both writes, who allowed each, and their outcomes.
  const deadline = Date.now() + 420 * 1000;
  let outcomes = [];
  for (;;) {
    await visit(requester.page, 'control-room');
    await requester.page.getByRole('tab', { name: 'Runs' }).click();
    await requester.page.getByTitle(plan.execution_id).click();
    await requester.page.getByRole('heading', { name: 'Writes' }).waitFor();
    await requester.page.waitForTimeout(500);
    outcomes = await requester.page
      .locator('.operate-trace li')
      .evaluateAll((items) => items.map((item) => item.innerText.replace(/\s+/g, ' ').trim()));
    if (outcomes.length === 2 && outcomes.every((text) => /Outcome: verified/i.test(text))) break;
    if (Date.now() > deadline) break;
    await requester.page.waitForTimeout(2000);
  }
  step('control room writes', { outcomes });
  await shot(requester.page, 'control-room-desktop');
  for (const width of ['tablet', 'phone']) {
    const viewer = await person('requester', width);
    await visit(viewer.page, 'control-room');
    await viewer.page.getByRole('tab', { name: 'Runs' }).click();
    await viewer.page.getByTitle(plan.execution_id).click();
    await viewer.page.getByRole('heading', { name: 'Writes' }).waitFor();
    await viewer.page.waitForTimeout(500);
    const overflow = await viewer.page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    result.steps.push({ name: `control room at ${width}`, horizontal_overflow_px: overflow });
    await shot(viewer.page, `control-room-${width}`);
    await viewer.context.close();
  }
  result.ok = true;
} catch (error) {
  result.ok = false;
  result.error = String(error?.message ?? error).slice(0, 600);
  for (const [index, [who, page]] of pages.entries()) {
    await shot(page, `failure-${index}-${who}`).catch(() => {});
    result.steps.push({
      name: 'failure page',
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
