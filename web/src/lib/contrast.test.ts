import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

// Reads the design tokens straight from the stylesheets, so a token change that drops text or
// a control edge below its WCAG 2.2 contrast floor fails here instead of in an audit.
const styles = new URL('../styles/', import.meta.url);
const consoleCss = readFileSync(new URL('console.css', styles), 'utf8');
const homeCss = readFileSync(new URL('home.css', styles), 'utf8');

function block(css: string, selector: string): string {
  const start = css.indexOf(`${selector} {`);
  assert.notEqual(start, -1, `${selector} is declared`);
  return css.slice(start, css.indexOf('}', start));
}
function tokens(css: string, selector: string): Record<string, string> {
  return Object.fromEntries(
    [...block(css, selector).matchAll(/(--[\w-]+):\s*(#[0-9a-f]{6})\b/gi)].map(
      ([, name, value]) => [name, value.toLowerCase()],
    ),
  );
}

// WCAG 2.2 relative luminance and contrast ratio.
function luminance(hex: string): number {
  const channel = (offset: number) => {
    const value = parseInt(hex.slice(offset, offset + 2), 16) / 255;
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(1) + 0.7152 * channel(3) + 0.0722 * channel(5);
}
function contrast(a: string, b: string): number {
  const [light, dark] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (light + 0.05) / (dark + 0.05);
}

const app = tokens(consoleCss, ':root,\n.console-window');
// The signed-out introduction's design system values (home.css, from tokens.json).
const home = tokens(homeCss, '.home');
// Table headers, wells and the form panel are drawn on --sunken (console.css).
const tableHead = app['--sunken'];
const formPanel = app['--sunken'];

function atLeast(floor: number, pairs: [string, string, string][]) {
  for (const [label, foreground, background] of pairs) {
    const ratio = contrast(foreground, background);
    assert.ok(ratio >= floor, `${label}: ${ratio.toFixed(2)} is below ${floor}:1`);
  }
}

test('the ratio matches the WCAG definition at its extremes', () => {
  assert.equal(contrast('#000000', '#ffffff'), 21);
  assert.equal(contrast('#777777', '#777777'), 1);
});

test('console text meets 4.5:1 on every surface it is set on', () => {
  atLeast(4.5, [
    ['ink on paper', app['--ink'], app['--paper']],
    ['ink on canvas', app['--ink'], app['--canvas']],
    ['muted on paper', app['--muted'], app['--paper']],
    ['muted on canvas', app['--muted'], app['--canvas']],
    ['muted on sunken', app['--muted'], app['--sunken']],
    ['muted on table header', app['--muted'], tableHead],
    ['accent link on paper', app['--accent'], app['--paper']],
    ['accent link on canvas', app['--accent'], app['--canvas']],
    ['accent link on sunken', app['--accent'], app['--sunken']],
    ['selected item on accent-soft', app['--accent'], app['--accent-soft']],
    ['decision label', '#ffffff', app['--decide']],
    ['ink on a waiting row', app['--ink'], app['--decide-soft']],
    ['button text on accent', '#ffffff', app['--accent']],
    ['success badge', app['--success'], app['--success-soft']],
    ['warning badge', app['--warning'], app['--warning-soft']],
    ['danger badge', app['--danger'], app['--danger-soft']],
    ['neutral badge', app['--muted'], app['--neutral-soft']],
  ]);
});

test('field edges and the focus ring meet 3:1 wherever they are drawn', () => {
  atLeast(3, [
    ['field edge on paper', app['--field-line'], app['--paper']],
    ['field edge on the form panel', app['--field-line'], formPanel],
    ['focus on paper', app['--focus'], app['--paper']],
    ['focus on canvas', app['--focus'], app['--canvas']],
    ['focus on sunken', app['--focus'], app['--sunken']],
    ['focus on the sage field', home['--pine-900'], home['--sage-500']],
    ['focus on a sheet', home['--pine-900'], home['--off-white']],
    ['focus on the pine chapter', home['--on-deep'], home['--pine-900']],
    ['outline button and tag edge on a sheet', home['--line-control'], home['--off-white']],
    ['outline button edge on a tinted panel', home['--line-control'], home['--sage-100']],
  ]);
});

test('public page text meets 4.5:1 on the field, the sheets and the pine chapter', () => {
  atLeast(4.5, [
    ['ink on the sage field', home['--pine-900'], home['--sage-500']],
    ['muted text on the sage field', home['--on-field-muted'], home['--sage-500']],
    ['ink on a sheet', home['--pine-900'], home['--off-white']],
    ['muted text on a sheet', home['--ink-muted'], home['--off-white']],
    ['muted text on a tinted panel', home['--ink-muted'], home['--sage-100']],
    ['sage labels on a sheet', home['--sage-700'], home['--off-white']],
    ['sage labels on a tinted panel', home['--sage-700'], home['--sage-100']],
    ['ink on a chip', home['--pine-900'], home['--off-white']],
    ['ink on a live tag', home['--pine-900'], home['--sage-200']],
    ['ink button label', home['--off-white'], home['--pine-900']],
    ['text on the pine chapter', home['--on-deep'], home['--pine-900']],
    ['muted text on the pine chapter', home['--on-deep-muted'], home['--pine-900']],
    ['code output on its ground', home['--on-deep-muted'], home['--deep-raised']],
  ]);
});

test('tomato never sits on the sage field, and holds 3:1 where the page marks with it', () => {
  assert.ok(contrast(home['--tomato-500'], home['--sage-500']) < 3, 'tomato would read on sage');
  atLeast(3, [
    ['underline on a sheet', home['--tomato-500'], home['--off-white']],
    ['underline on the pine chapter', home['--tomato-500'], home['--pine-900']],
  ]);
});

test('tomato is a mark, not text: it holds 3:1 where the wordmark dot is drawn', () => {
  atLeast(3, [
    ['dot on paper', app['--lotus'], app['--paper']],
    ['dot on sunken', app['--lotus'], app['--sunken']],
    ['dot on canvas', app['--lotus'], app['--canvas']],
  ]);
});
