import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { createServer } from 'node:http';
import test from 'node:test';
import { promisify } from 'node:util';
import { createClient } from './client.mjs';
const config = { baseUrl: 'http://127.0.0.1:8000/api/v1', apiKey: 'synthetic-key' };
const executeFile = promisify(execFile);

async function localServer(t, handler) {
  const server = createServer(handler);
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  t.after(
    () =>
      new Promise((resolve) => {
        server.closeAllConnections();
        server.close(resolve);
      }),
  );
  return { server, url: `http://127.0.0.1:${server.address().port}/api/v1` };
}

test('origin-only loopback API URLs share the /api/v1 base used by packaged commands', async () => {
  let href;
  const request = createClient({
    baseUrl: 'http://127.0.0.1:8000',
    apiKey: 'synthetic-key',
    fetchImpl: async (url) => {
      href = url.href;
      return Response.json({ ok: true });
    },
  });
  await request('/runs');
  assert.equal(href, 'http://127.0.0.1:8000/api/v1/runs');
});

test('client preserves API contract and denies destination escapes', async () => {
  let called = 0;
  const request = createClient({
    ...config,
    fetchImpl: async (url, options) => {
      called++;
      assert.equal(url.href, config.baseUrl + '/runs');
      assert.equal(options.redirect, 'error');
      assert.equal(options.headers.authorization, 'Bearer synthetic-key');
      assert.equal(options.method, 'POST');
      assert.equal(options.body, '{"name":"synthetic"}');
      return Response.json({ ok: true });
    },
  });
  assert.deepEqual(await request('/runs', { name: 'synthetic' }), { ok: true });
  for (const path of ['//outside.test', '/../outside', '/%2e%2e/outside', '/runs#fragment'])
    await assert.rejects(request(path), { code: 'invalid_path' });
  assert.equal(called, 1);
  for (const baseUrl of [
    'http://outside.test',
    'https://user:secret@example.test',
    'https://example.test?token=secret',
    'https://example.test/#fragment',
  ])
    assert.throws(() => createClient({ ...config, baseUrl }), { code: 'invalid_configuration' });
});

test('client bounds outgoing and streamed incoming JSON and cancels overflow', async () => {
  let canceled = false;
  let calls = 0;
  const request = createClient({
    ...config,
    fetchImpl: async () => {
      calls++;
      return new Response(
        new ReadableStream({
          pull(controller) {
            controller.enqueue(new Uint8Array(1024 * 1024));
          },
          cancel() {
            canceled = true;
          },
        }),
      );
    },
  });
  await assert.rejects(request('/runs', { value: 'x'.repeat(2 * 1024 * 1024) }), {
    code: 'request_too_large',
  });
  assert.equal(calls, 0);
  await assert.rejects(request('/runs'), { code: 'response_too_large' });
  assert.equal(canceled, true);
});

test('client sanitizes server and transport errors and rejects malformed JSON', async () => {
  for (const status of [401, 403, 409, 500]) {
    const request = createClient({
      ...config,
      fetchImpl: async () => new Response('private-token-in-error', { status }),
    });
    await assert.rejects(request('/runs'), (error) => {
      assert.equal(error.status, status);
      assert.equal(error.message.includes('private-token'), false);
      return true;
    });
  }
  const invalid = createClient({ ...config, fetchImpl: async () => new Response('{invalid') });
  await assert.rejects(invalid('/runs'), { code: 'invalid_response' });
  const failure = createClient({
    ...config,
    fetchImpl: async () => {
      throw new Error('private-token');
    },
  });
  await assert.rejects(failure('/runs'), { code: 'request_failed' });
});

test('client cancellation and deadline cover body consumption after headers', async () => {
  const request = createClient({
    ...config,
    fetchImpl: async (_url, { signal }) => {
      return new Response(
        new ReadableStream({
          start(controller) {
            signal.addEventListener('abort', () => controller.error(signal.reason), { once: true });
          },
        }),
      );
    },
  });
  const keepAlive = setInterval(() => {}, 1000);
  try {
    await assert.rejects(request('/runs', undefined, { timeoutMs: 15 }), { code: 'timeout' });
    const controller = new AbortController();
    const pending = request('/runs', undefined, { signal: controller.signal });
    controller.abort();
    await assert.rejects(pending, { code: 'aborted' });
  } finally {
    clearInterval(keepAlive);
  }
});

test('default client is direct even when Node starts with ambient proxy support enabled', async (t) => {
  if (!process.allowedNodeEnvironmentFlags.has('--use-env-proxy'))
    return t.skip('Node does not support ambient proxy mode');
  let proxyHits = 0;
  let targetHits = 0;
  let authorized = false;
  const proxy = await localServer(t, (_request, response) => {
    proxyHits += 1;
    response.writeHead(502).end();
  });
  proxy.server.on('connect', (_request, socket) => {
    proxyHits += 1;
    socket.end('HTTP/1.1 502 Bad Gateway\r\n\r\n');
  });
  const target = await localServer(t, (request, response) => {
    targetHits += 1;
    authorized = request.headers.authorization === 'Bearer synthetic-review-key';
    response.writeHead(200, { 'content-type': 'application/json' }).end('{"ok":true}');
  });
  const moduleUrl = new URL('./client.mjs', import.meta.url).href;
  const source = `import {createClient} from ${JSON.stringify(moduleUrl)};
    const request=createClient({baseUrl:${JSON.stringify(target.url)},apiKey:process.env.LOTUSPOND_TEST_KEY});
    const result=await request('/runs');
    if(result.ok!==true)process.exitCode=1;`;
  const { stdout, stderr } = await executeFile(
    process.execPath,
    ['--use-env-proxy', '--input-type=module', '-e', source],
    {
      env: {
        HTTP_PROXY: new URL(proxy.url).origin,
        HTTPS_PROXY: new URL(proxy.url).origin,
        ALL_PROXY: new URL(proxy.url).origin,
        NO_PROXY: '',
        NODE_USE_ENV_PROXY: '1',
        LOTUSPOND_TEST_KEY: 'synthetic-review-key',
      },
      timeout: 5000,
      maxBuffer: 4096,
    },
  );
  assert.equal(targetHits, 1);
  assert.equal(authorized, true);
  assert.equal(proxyHits, 0);
  assert.equal(
    stdout.includes('synthetic-review-key') || stderr.includes('synthetic-review-key'),
    false,
  );
});

test('direct transport denies redirect credential forwarding and compressed success bodies', async (t) => {
  let redirectedHits = 0;
  const target = await localServer(t, (_request, response) => {
    redirectedHits += 1;
    response.writeHead(200).end('{}');
  });
  const source = await localServer(t, (request, response) => {
    assert.equal(request.headers['accept-encoding'], 'identity');
    if (request.url.endsWith('/redirect')) response.writeHead(307, { location: target.url }).end();
    else response.writeHead(200, { 'content-encoding': 'gzip' }).end('{"ok":true}');
  });
  const request = createClient({ ...config, baseUrl: source.url });
  await assert.rejects(request('/redirect'), { code: 'request_failed', status: 307 });
  assert.equal(redirectedHits, 0);
  await assert.rejects(request('/compressed'), { code: 'invalid_response' });
});

test('direct response stream obeys deadline and cancellation after headers and partial JSON', async (t) => {
  const controller = new AbortController();
  let closed = 0;
  const server = await localServer(t, (request, response) => {
    response.writeHead(200, { 'content-type': 'application/json' });
    response.write('{"value":"');
    const timer = setInterval(() => response.write('a'), 20);
    response.once('close', () => {
      clearInterval(timer);
      closed += 1;
    });
    if (request.url.endsWith('/cancel')) controller.abort();
  });
  const request = createClient({ ...config, baseUrl: server.url });
  await assert.rejects(request('/deadline', undefined, { timeoutMs: 100 }), { code: 'timeout' });
  await assert.rejects(request('/cancel', undefined, { signal: controller.signal }), {
    code: 'aborted',
  });
  await new Promise((resolve) => setTimeout(resolve, 30));
  assert.equal(closed, 2);
});

test('direct response rejects oversized declared/chunked bodies and incomplete JSON', async (t) => {
  const server = await localServer(t, (request, response) => {
    if (request.url.endsWith('/declared'))
      response.writeHead(200, { 'content-length': 9 * 1024 * 1024 }).end('{}');
    else if (request.url.endsWith('/chunked')) {
      response.writeHead(200, { 'content-type': 'application/json' });
      response.write('{"value":"');
      response.end('a'.repeat(9 * 1024 * 1024));
    } else response.writeHead(200, { 'content-length': 100 }).end('{}');
  });
  const request = createClient({ ...config, baseUrl: server.url });
  await assert.rejects(request('/declared'), { code: 'response_too_large' });
  await assert.rejects(request('/chunked'), { code: 'response_too_large' });
  await assert.rejects(request('/incomplete'), { code: 'request_failed' });
});
