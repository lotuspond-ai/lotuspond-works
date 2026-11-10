import assert from 'node:assert/strict';
import test from 'node:test';
import { createIdentityLinks, parseIdentityLink } from './identityLinks.js';

const token = 'synthetic_test_token_1234567890';
function fixture() {
  const data = new Map<string, string>();
  return {
    data,
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => {
      data.set(key, value);
    },
    removeItem: (key: string) => {
      data.delete(key);
    },
  };
}
test('email links retain the action while removing token from browser location', () => {
  for (const page of ['verify-email', 'reset-password', 'accept-invitation']) {
    const parsed = parseIdentityLink(`https://lotuspond.example/#${page}?token=${token}`);
    assert.equal(parsed.page, page);
    assert.equal(parsed.token, token);
    assert.equal(parsed.cleanHref, `https://lotuspond.example/#${page}`);
  }
});
test('duplicate, malformed, and misplaced tokens are scrubbed and never accepted', () => {
  for (const href of [
    `https://lotuspond.example/#verify-email?token=${token}&token=${token}`,
    'https://lotuspond.example/#reset-password?token=%3Cscript%3E',
    `https://lotuspond.example/?token=${token}#releases`,
    `https://lotuspond.example/#releases?token=${token}`,
    // Offline enrollment is retired (ADR-0014); its links are scrubbed like any other.
    `https://lotuspond.example/#enroll?token=${token}`,
  ]) {
    const parsed = parseIdentityLink(href);
    assert.equal(parsed.token, undefined);
    assert.equal(parsed.supplied, true);
    assert.equal(parsed.cleanHref.includes('token='), false);
  }
});
test('only invitation survives a tab reload; login navigation does not discard it', () => {
  const storage = fixture();
  const links = createIdentityLinks(storage);
  const history = {
    state: null,
    replaceState: (_: unknown, __: string, url?: string | URL | null) => {
      assert.equal(String(url).includes(token), false);
    },
  };
  links.capture({ href: `https://lotuspond.example/#accept-invitation?token=${token}` }, history);
  links.capture({ href: `https://lotuspond.example/#verify-email?token=${token}` }, history);
  links.capture({ href: 'https://lotuspond.example/#login' }, history);
  assert.equal(links.token('accept-invitation'), token);
  const reloaded = createIdentityLinks(storage);
  assert.equal(reloaded.token('accept-invitation'), token);
  assert.equal(reloaded.token('verify-email'), undefined);
  reloaded.clear('accept-invitation');
  assert.equal(storage.data.size, 0);
});
test('local expiry clears invitation storage and stale tokens cannot be reused', () => {
  const storage = fixture();
  let time = 1_000;
  const links = createIdentityLinks(storage, () => time);
  links.capture(
    { href: `https://lotuspond.example/#accept-invitation?token=${token}` },
    { state: null, replaceState: () => {} },
  );
  time += 3_600_001;
  assert.equal(links.token('accept-invitation'), undefined);
  assert.equal(storage.data.size, 0);
});

test('a retired enrollment link loses its token before rendering and keeps nothing', () => {
  const storage = fixture();
  const links = createIdentityLinks(storage);
  let scrubbed = '';
  links.capture(
    { href: `https://lotuspond.example/#enroll?token=${token}` },
    {
      state: null,
      replaceState: (_, __, url) => {
        scrubbed = String(url);
      },
    },
  );
  assert.equal(scrubbed, 'https://lotuspond.example/#enroll');
  assert.equal(links.token('accept-invitation'), undefined);
  assert.equal(storage.data.size, 0);
});
