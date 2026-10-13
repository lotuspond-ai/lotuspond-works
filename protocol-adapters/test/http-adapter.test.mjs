import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { afterEach, test } from 'node:test';
import { evaluate } from '../src/http-runner.mjs';

const servers = [];
afterEach(async () => {
  await Promise.all(
    servers.splice(0).map(
      (server) =>
        new Promise((resolve) => {
          server.closeAllConnections();
          server.close(resolve);
        }),
    ),
  );
});

async function endpoint(
  handler = (_request, response) => {
    response
      .writeHead(200, { 'content-type': 'application/json' })
      .end('{"text":"approved answer"}');
  },
) {
  const requests = [];
  const server = createServer(async (request, response) => {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    requests.push({
      method: request.method,
      headers: request.headers,
      body: JSON.parse(Buffer.concat(chunks).toString('utf8')),
    });
    handler(request, response);
  });
  servers.push(server);
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  return { url: `http://127.0.0.1:${server.address().port}/agent`, requests };
}

function env(url, extra = {}) {
  return { LOTUSPOND_HTTP_ENDPOINT: url, LOTUSPOND_HTTP_TIMEOUT_MS: '2000', ...extra };
}

async function run(
  url,
  extra = {},
  expected = { contains: 'approved' },
  input = { prompt: 'hello' },
  context = {},
) {
  return evaluate(input, expected, context, { env: env(url, extra) });
}

test('plain-input endpoint uses operator bearer auth, returns deterministic evidence, and does not invent cost', async () => {
  const server = await endpoint();
  const token = 'synthetic-http-adapter-credential';
  const result = await run(server.url, { LOTUSPOND_HTTP_TOKEN: token });
  assert.equal(result.passed, true);
  assert.deepEqual(server.requests[0].body, { input: 'hello' });
  assert.equal(server.requests[0].method, 'POST');
  assert.equal(server.requests[0].headers.authorization === `Bearer ${token}`, true);
  assert.equal(result.evidence.outputComplete, true);
  assert.equal(result.evidence.cost_measurement, 'unavailable');
  assert.equal(Object.hasOwn(result, 'costUsd'), false);
  assert.equal(JSON.stringify(result).includes(token), false);
  assert.equal((await run(server.url, {}, { equals: 'different answer' })).passed, false);
});

test('OpenAI chat maps only configured model/system instructions and extracts actual message text', async () => {
  const server = await endpoint((_request, response) => {
    response
      .writeHead(200, { 'content-type': 'application/json; charset=utf-8' })
      .end(JSON.stringify({ choices: [{ message: { content: 'approved answer' } }] }));
  });
  const result = await run(
    server.url,
    {
      LOTUSPOND_HTTP_MODE: 'openai-chat',
      LOTUSPOND_HTTP_MODEL: 'operator-model',
      LOTUSPOND_HTTP_SYSTEM_PROMPT: 'Be concise.',
    },
    { equals: 'approved answer' },
  );
  assert.equal(result.passed, true);
  assert.deepEqual(server.requests[0].body, {
    model: 'operator-model',
    messages: [
      { role: 'system', content: 'Be concise.' },
      { role: 'user', content: 'hello' },
    ],
  });
  assert.equal(result.evidence.requestMode, 'openai-chat');
});

test('operator response path handles array indices; missing text cannot pass an assertion', async () => {
  const server = await endpoint((_request, response) => {
    response
      .writeHead(200, { 'content-type': 'application/json' })
      .end('{"result":[{"reply":"approved answer"}],"text":"misleading"}');
  });
  assert.equal(
    (await run(server.url, { LOTUSPOND_HTTP_RESPONSE_PATH: 'result[0].reply' })).passed,
    true,
  );
  const missing = await run(server.url, { LOTUSPOND_HTTP_RESPONSE_PATH: 'result[1].reply' });
  assert.equal(missing.passed, null);
  assert.equal(missing.output.error, 'output_missing');
});

test('plain text works, while absent, empty, ambiguous, or unsupported criteria remain unassessed', async () => {
  const server = await endpoint((_request, response) =>
    response.writeHead(200, { 'content-type': 'text/plain' }).end('approved answer'),
  );
  assert.equal((await run(server.url)).passed, true);
  for (const expected of [
    null,
    {},
    { contains: '' },
    { equals: ' ' },
    { regex: 'approved' },
    { contains: 'approved', equals: 'approved answer' },
    { equals: { text: 'approved answer' } },
  ]) {
    const result = await run(server.url, {}, expected);
    assert.equal(result.passed, null);
    assert.equal(result.evidence.outcome, 'unassessed');
    assert.equal(result.evidence.outputComplete, true);
  }
});

test('scenario input cannot override endpoint, headers, auth, model, or request mode', async () => {
  const server = await endpoint();
  for (const field of ['url', 'endpoint', 'headers', 'token', 'model', 'mode']) {
    const result = await run(
      server.url,
      {},
      { contains: 'approved' },
      { prompt: 'hello', [field]: 'override' },
    );
    assert.equal(result.passed, null);
    assert.equal(result.output.error, 'invalid_input');
  }
  assert.equal(server.requests.length, 0);
});

test('input bound covers UTF-8 and complete mapped request before opening a connection', async () => {
  const server = await endpoint();
  for (const [input, extra] of [
    [{ prompt: '🙂'.repeat(300) }, {}],
    [
      { prompt: 'hello' },
      { LOTUSPOND_HTTP_MODE: 'openai-chat', LOTUSPOND_HTTP_SYSTEM_PROMPT: 's'.repeat(1024) },
    ],
  ]) {
    const result = await run(
      server.url,
      { LOTUSPOND_HTTP_MAX_INPUT_BYTES: '1024', ...extra },
      { contains: 'approved' },
      input,
    );
    assert.equal(result.output.error, 'input_too_large');
  }
  assert.equal(server.requests.length, 0);
});

test('refuses redirects without contacting or forwarding authorization to the target', async () => {
  const target = await endpoint();
  const redirect = await endpoint((_request, response) =>
    response.writeHead(307, { location: target.url }).end(),
  );
  const result = await run(redirect.url, { LOTUSPOND_HTTP_TOKEN: 'synthetic-redirect-credential' });
  assert.equal(result.passed, null);
  assert.equal(result.output.error, 'redirect_refused');
  assert.equal(target.requests.length, 0);
});

test('full-operation timeout terminates slow response bodies even when headers and chunks arrive', async () => {
  let closed = false;
  const server = await endpoint((_request, response) => {
    response.writeHead(200, { 'content-type': 'text/plain' });
    response.write('approved');
    const timer = setInterval(() => response.write(' '), 30);
    response.on('close', () => {
      clearInterval(timer);
      closed = true;
    });
  });
  const started = performance.now();
  const result = await run(server.url, { LOTUSPOND_HTTP_TIMEOUT_MS: '250' });
  assert.equal(result.passed, null);
  assert.equal(result.output.error, 'timeout');
  assert.equal(performance.now() - started < 1500, true);
  await new Promise((resolve) => setTimeout(resolve, 30));
  assert.equal(closed, true);
});

test('deadline also covers DNS; cancelled operations never start a request', async () => {
  const started = performance.now();
  const result = await evaluate(
    { prompt: 'hello' },
    { contains: 'approved' },
    {},
    {
      env: env('https://agent.example.test/agent', { LOTUSPOND_HTTP_TIMEOUT_MS: '250' }),
      lookup: () => new Promise(() => {}),
    },
  );
  assert.equal(result.output.error, 'timeout');
  assert.equal(performance.now() - started < 1500, true);
  const server = await endpoint();
  const controller = new AbortController();
  controller.abort();
  assert.equal(
    (
      await run(
        server.url,
        {},
        { contains: 'approved' },
        { prompt: 'hello' },
        { signal: controller.signal },
      )
    ).output.error,
    'cancelled',
  );
  assert.equal(server.requests.length, 0);
});

test('caller cancellation destroys an active request and retains unknown evidence', async () => {
  const controller = new AbortController();
  const server = await endpoint((_request, response) => {
    response.writeHead(200, { 'content-type': 'text/plain' });
    response.write('approved');
    controller.abort();
  });
  const result = await run(
    server.url,
    {},
    { contains: 'approved' },
    { prompt: 'hello' },
    { signal: controller.signal },
  );
  assert.equal(result.passed, null);
  assert.equal(result.output.error, 'cancelled');
});

test('declared and streamed oversized responses never assess partial text', async () => {
  for (const declared of [true, false]) {
    const server = await endpoint((_request, response) => {
      response.writeHead(200, {
        'content-type': 'text/plain',
        ...(declared ? { 'content-length': '3000' } : {}),
      });
      response.end(`approved${'x'.repeat(2000)}`);
    });
    const result = await run(server.url, { LOTUSPOND_HTTP_MAX_RESPONSE_BYTES: '1024' });
    assert.equal(result.passed, null);
    assert.equal(result.output.error, 'response_too_large');
    assert.equal(result.evidence.outputComplete, false);
    assert.equal(Object.hasOwn(result.output, 'text'), false);
  }
});

test('complete response with excessive retained text is unknown without truncation', async () => {
  const server = await endpoint((_request, response) =>
    response.writeHead(200, { 'content-type': 'text/plain' }).end(`approved${'x'.repeat(2000)}`),
  );
  const result = await run(server.url, { LOTUSPOND_HTTP_MAX_OUTPUT_BYTES: '1024' });
  assert.equal(result.passed, null);
  assert.equal(result.output.error, 'output_too_large');
});

test('HTTP errors and incomplete, invalid, unsupported, or empty output never become failed agent verdicts', async () => {
  for (const [status, headers, body, code] of [
    [401, { 'content-type': 'text/plain' }, 'sensitive provider body', 'authentication_failed'],
    [503, { 'content-type': 'text/plain' }, 'sensitive provider body', 'http_error'],
    [200, { 'content-type': 'application/json' }, '{"text":"approved"', 'invalid_response'],
    [200, { 'content-type': 'application/json' }, '{"text":""}', 'output_missing'],
    [200, { 'content-type': 'text/html' }, '<html>approved</html>', 'unsupported_content_type'],
    [
      200,
      { 'content-type': 'text/plain', 'content-encoding': 'gzip' },
      'approved',
      'unsupported_encoding',
    ],
    [
      200,
      { 'content-type': 'text/plain', 'content-length': '100' },
      'approved',
      'response_incomplete',
    ],
    [200, { 'content-type': 'text/plain' }, Buffer.from([0xff]), 'invalid_response'],
  ]) {
    const server = await endpoint((_request, response) =>
      response.writeHead(status, headers).end(body),
    );
    const result = await run(server.url);
    assert.equal(result.passed, null);
    assert.equal(result.output.error, code);
    assert.equal(JSON.stringify(result).includes('sensitive provider body'), false);
  }
});

test('rejects URL credentials, insecure external URLs, invalid config, and unsafe response paths before DNS', async () => {
  let resolutions = 0;
  for (const [url, extra] of [
    ['http://agent.example.test/agent', {}],
    ['https://user:password@agent.example.test/agent', {}],
    ['https://agent.example.test/agent?key=not-allowed', {}],
    ['https://agent.example.test/agent#fragment', {}],
    ['https://agent.example.test/agent', { LOTUSPOND_HTTP_RESPONSE_PATH: '__proto__.text' }],
    ['https://agent.example.test/agent', { LOTUSPOND_HTTP_TIMEOUT_MS: '0' }],
    ['https://agent.example.test/agent', { LOTUSPOND_HTTP_TOKEN: 'bad\r\nheader' }],
  ]) {
    const result = await evaluate(
      { prompt: 'hello' },
      { contains: 'approved' },
      {},
      {
        env: env(url, extra),
        lookup: () => {
          resolutions += 1;
          return [];
        },
      },
    );
    assert.equal(result.passed, null);
  }
  assert.equal(resolutions, 0);
});

test('airgap denies external hosts before DNS but permits loopback; localhost resolution is verified and pinned', async () => {
  let resolutions = 0;
  const external = await evaluate(
    { prompt: 'hello' },
    { contains: 'approved' },
    {},
    {
      env: env('https://agent.example.test/agent', { LOTUSPOND_NETWORK_MODE: 'airgap' }),
      lookup: () => {
        resolutions += 1;
        return [];
      },
    },
  );
  assert.equal(external.output.error, 'airgap');
  assert.equal(resolutions, 0);
  const server = await endpoint();
  assert.equal((await run(server.url, { LOTUSPOND_NETWORK_MODE: 'airgap' })).passed, true);
  const localUrl = server.url.replace('127.0.0.1', 'localhost');
  const blocked = await evaluate(
    { prompt: 'hello' },
    { contains: 'approved' },
    {},
    {
      env: env(localUrl),
      lookup: async () => [{ address: '192.0.2.1', family: 4 }],
    },
  );
  assert.equal(blocked.output.error, 'endpoint_rejected');
  const local = await evaluate(
    { prompt: 'hello' },
    { contains: 'approved' },
    {},
    {
      env: env(localUrl),
      lookup: async () => {
        resolutions += 1;
        return [{ address: '127.0.0.1', family: 4 }];
      },
    },
  );
  assert.equal(local.passed, true);
  assert.equal(resolutions, 1);
});

test('explicit direct agent ignores ambient HTTP and HTTPS proxy configuration', async () => {
  const proxy = await endpoint();
  const target = await endpoint();
  const names = [
    'HTTP_PROXY',
    'HTTPS_PROXY',
    'ALL_PROXY',
    'http_proxy',
    'https_proxy',
    'all_proxy',
    'NODE_USE_ENV_PROXY',
  ];
  const saved = names.map((name) => [name, process.env[name]]);
  try {
    for (const name of names) process.env[name] = name === 'NODE_USE_ENV_PROXY' ? '1' : proxy.url;
    assert.equal((await run(target.url)).passed, true);
    assert.equal(proxy.requests.length, 0);
  } finally {
    for (const [name, value] of saved) {
      if (value === undefined) delete process.env[name];
      else process.env[name] = value;
    }
  }
});

test('an endpoint echoing the configured bearer token cannot persist that token in evidence', async () => {
  const token = 'synthetic-echoed-http-credential';
  const server = await endpoint((_request, response) =>
    response.writeHead(200, { 'content-type': 'text/plain' }).end(token),
  );
  const result = await run(server.url, { LOTUSPOND_HTTP_TOKEN: token });
  assert.equal(result.passed, null);
  assert.equal(result.output.error, 'sensitive_output');
  assert.equal(JSON.stringify(result).includes(token), false);
});
