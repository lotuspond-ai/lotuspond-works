import assert from 'node:assert/strict';
import test from 'node:test';

import worker from './worker.ts';

function fixture(options: { failStorage?: boolean; limited?: boolean } = {}) {
  const records = new Map<string, string>();
  const env = {
    ASSETS: { fetch: async () => new Response('landing') },
    INTEREST: {
      put: async (key: string, value: string) => {
        if (options.failStorage) throw new Error('private storage error');
        records.set(key, value);
      },
    },
    SIGNUP_LIMIT: { limit: async () => ({ success: !options.limited }) },
  };
  return { env, records };
}
function request(body: unknown, headers: Record<string, string> = {}) {
  return new Request('https://lotuspond.ai/api/interest', {
    method: 'POST',
    headers: {
      Origin: 'https://lotuspond.ai',
      'Content-Type': 'application/json',
      'CF-Connecting-IP': '192.0.2.1',
      ...headers,
    },
    body: JSON.stringify(body),
  });
}
const valid = { email: 'qa@example.com', consent: true, website: '' };

test('normalizes and stores only email, timestamp, source and consent; duplicates use one key', async () => {
  const { env, records } = fixture();
  assert.equal(
    (await worker.fetch(request({ ...valid, email: ' QA@Example.com ' }), env)).status,
    200,
  );
  assert.equal((await worker.fetch(request(valid), env)).status, 200);
  assert.equal(records.size, 1);
  const [key, value] = [...records][0];
  assert.match(key, /^email:[a-f0-9]{64}$/);
  const record = JSON.parse(value);
  assert.equal(record.email, 'qa@example.com');
  assert.equal(record.consent, 'lotuspond-early-access-v1');
  assert.deepEqual(Object.keys(record).sort(), ['consent', 'email', 'registeredAt', 'source']);
});
test('rejects malformed email, missing consent, unexpected fields and bot honeypot without storage', async () => {
  const { env, records } = fixture();
  for (const body of [
    null,
    [],
    {},
    { ...valid, email: 'a@' },
    { ...valid, email: 'a..b@example.com' },
    { ...valid, consent: false },
    { ...valid, website: 'spam' },
    { ...valid, extra: 'unknown' },
  ]) {
    assert.equal((await worker.fetch(request(body), env)).status, 400);
  }
  assert.equal(records.size, 0);
});
test('rejects cross-origin, missing origin, unsupported content type and oversized bodies', async () => {
  const { env, records } = fixture();
  assert.equal(
    (await worker.fetch(request(valid, { Origin: 'https://evil.example' }), env)).status,
    403,
  );
  assert.equal((await worker.fetch(request(valid, { Origin: '' }), env)).status, 403);
  assert.equal(
    (await worker.fetch(request(valid, { 'Content-Type': 'text/plain' }), env)).status,
    415,
  );
  assert.equal(
    (await worker.fetch(request({ ...valid, email: 'a'.repeat(3000) }), env)).status,
    400,
  );
  assert.equal(records.size, 0);
});
test('rate limits without storage and never exposes private storage failure', async () => {
  const limited = fixture({ limited: true });
  assert.equal((await worker.fetch(request(valid), limited.env)).status, 429);
  assert.equal(limited.records.size, 0);
  const response = await worker.fetch(request(valid), fixture({ failStorage: true }).env);
  assert.equal(response.status, 503);
  assert.doesNotMatch(await response.text(), /private storage error/);
});
test('there is no public email list; security headers protect API and page', async () => {
  const { env } = fixture();
  const response = await worker.fetch(new Request('https://lotuspond.ai/api/interest'), env);
  assert.equal(response.status, 405);
  assert.equal(response.headers.get('Cache-Control'), 'no-store');
  const page = await worker.fetch(new Request('https://lotuspond.ai/'), env);
  assert.equal(page.headers.get('X-Frame-Options'), 'DENY');
  assert.match(page.headers.get('Content-Security-Policy') ?? '', /script-src 'self'/);
});

test('email queue failure preserves the registration and reports the delay honestly', async () => {
  const { env, records } = fixture();
  const response = await worker.fetch(request(valid), {
    ...env,
    CONFIRMATIONS: {
      idFromName: (name: string) => name,
      get: () => ({
        fetch: async () => {
          throw new Error('queue unavailable');
        },
      }),
    },
  });
  assert.equal(response.status, 200);
  assert.equal(records.size, 1);
  assert.match(await response.text(), /confirmation email is delayed/);
});

test('email is never queued before storage, and prior failed deliveries do not claim success', async () => {
  let called = false;
  const CONFIRMATIONS = {
    idFromName: (name: string) => name,
    get: () => ({
      fetch: async () => {
        called = true;
        return Response.json({ status: 'unknown' });
      },
    }),
  };
  const failed = await worker.fetch(request(valid), {
    ...fixture({ failStorage: true }).env,
    CONFIRMATIONS,
  });
  assert.equal(failed.status, 503);
  assert.equal(called, false);
  const saved = await worker.fetch(request(valid), { ...fixture().env, CONFIRMATIONS });
  assert.equal(saved.status, 200);
  assert.match(await saved.text(), /couldn’t confirm email delivery/);
});
