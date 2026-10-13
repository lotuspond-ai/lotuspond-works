import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { afterEach, test } from 'node:test';
import { evaluate } from '../src/mcp-runner.mjs';
import { listAdvertisedTools } from '../src/mcp-tools.mjs';

const servers = [];

afterEach(async () => {
  await Promise.all(servers.splice(0).map((server) => new Promise((resolve) => server.close(resolve))));
});

async function mockMcpServer({ callResult, tools, responseHook } = {}) {
  const requests = [];
  const server = createServer(async (request, response) => {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    const body = chunks.length ? JSON.parse(Buffer.concat(chunks).toString('utf8')) : undefined;
    requests.push({ method: request.method, headers: request.headers, body });
    if (responseHook?.({ request, response, body })) return;
    if (request.method === 'DELETE') {
      response.writeHead(200).end();
      return;
    }
    if (body?.id === undefined) {
      response.writeHead(202).end();
      return;
    }
    let result;
    if (body.method === 'initialize') {
      result = {
        protocolVersion: body.params.protocolVersion,
        capabilities: { tools: {} },
        serverInfo: { name: 'local-mock', version: '1.0.0' },
      };
    } else if (body.method === 'tools/list') {
      result = { tools: tools ?? [{ name: 'approved.tool', description: 'Local test tool', inputSchema: {} }] };
    } else if (body.method === 'tools/call') {
      result = callResult ?? { content: [{ type: 'text', text: 'approved answer' }] };
    } else {
      response.writeHead(404).end();
      return;
    }
    const encoded = JSON.stringify({ jsonrpc: '2.0', id: body.id, result });
    response.writeHead(200, { 'content-type': 'application/json', 'content-length': Buffer.byteLength(encoded) });
    response.end(encoded);
  });
  servers.push(server);
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const address = server.address();
  return { server, requests, endpoint: `http://127.0.0.1:${address.port}/mcp` };
}

function env(endpoint, extra = {}) {
  return {
    LOTUSPOND_MCP_ENDPOINT: endpoint,
    LOTUSPOND_MCP_TOOL: 'approved.tool',
    LOTUSPOND_MCP_TOKEN: 'secret-test-token',
    LOTUSPOND_MCP_TIMEOUT_MS: '2000',
    ...extra,
  };
}

test('calls only the operator-configured tool over Streamable HTTP with bearer auth', async () => {
  const mock = await mockMcpServer();
  const result = await evaluate(
    { arguments: { prompt: 'hello' } },
    { contains: 'approved answer' },
    {},
    { env: env(mock.endpoint) },
  );

  assert.equal(result.passed, true);
  assert.equal(result.evidence.tool, 'approved.tool');
  assert.equal(result.evidence.cost_measurement, 'unavailable');
  const call = mock.requests.find((item) => item.body?.method === 'tools/call');
  assert.deepEqual(call.body.params, { name: 'approved.tool', arguments: { prompt: 'hello' } });
  assert.equal(call.headers.authorization, 'Bearer secret-test-token');
  assert.doesNotMatch(JSON.stringify(result), /secret-test-token/);
  assert.equal(mock.requests.some((item) => item.body?.method === 'tools/list'), false);
});

test('list-tools is a separate read-only advertised capability check', async () => {
  const mock = await mockMcpServer({
    tools: [
      {
        name: 'approved.tool',
        title: 'Approved',
        description: 'Advertised by the mock',
        inputSchema: { type: 'object' },
      },
      { name: 'other.tool', inputSchema: { type: 'object' } },
    ],
  });
  const result = await listAdvertisedTools({ env: env(mock.endpoint) });

  assert.deepEqual(result, {
    status: 'advertised',
    tools: [
      { name: 'approved.tool', title: 'Approved', description: 'Advertised by the mock' },
      { name: 'other.tool', title: undefined, description: undefined },
    ],
  });
  assert.equal(mock.requests.some((item) => item.body?.method === 'tools/call'), false);
});

test('missing configuration and airgap mode fail closed without constructing a client', async () => {
  let constructed = false;
  const connectionFactory = () => {
    constructed = true;
    throw new Error('must not run');
  };

  const missing = await evaluate({ arguments: {} }, { contains: 'x' }, {}, { env: {}, connectionFactory });
  const airgap = await evaluate(
    { arguments: {} },
    { contains: 'x' },
    {},
    { env: env('https://mcp.example.test/service', { LOTUSPOND_NETWORK_MODE: 'airgap' }), connectionFactory },
  );

  assert.equal(missing.passed, null);
  assert.equal(missing.output.error, 'configuration_missing');
  assert.equal(airgap.passed, null);
  assert.equal(airgap.output.error, 'airgap');
  assert.equal(constructed, false);
});

test('scenario input cannot select a destination or tool', async () => {
  const result = await evaluate(
    { endpoint: 'https://attacker.invalid', tool: 'other.tool', arguments: {} },
    { contains: 'x' },
    {},
    { env: env('https://mcp.example.test/service') },
  );
  assert.equal(result.passed, null);
  assert.equal(result.output.error, 'invalid_input');
});

test('server tool errors remain unknown and never become a passing verdict', async () => {
  const mock = await mockMcpServer({
    callResult: { isError: true, content: [{ type: 'text', text: 'approved answer' }] },
  });
  const result = await evaluate(
    { arguments: {} },
    { contains: 'approved answer' },
    {},
    { env: env(mock.endpoint) },
  );
  assert.equal(result.passed, null);
  assert.equal(result.evidence.outcome, 'tool_error');
});

test('caller cancellation has a distinct unknown outcome', async () => {
  const controller = new AbortController();
  const client = {
    async connect() {},
    async callTool(_params, options) {
      controller.abort();
      options.signal.throwIfAborted();
    },
    async close() {},
  };
  const result = await evaluate(
    { arguments: {} },
    { contains: 'x' },
    { signal: controller.signal },
    {
      env: env('https://mcp.example.test/service'),
      connectionFactory: () => ({ client, transport: {} }),
    },
  );
  assert.equal(result.passed, null);
  assert.equal(result.output.error, 'cancelled');
});

test('the configured timeout aborts an unresponsive tool call as unknown', async () => {
  const client = {
    async connect() {},
    async callTool(_params, options) {
      await new Promise((resolve, reject) => {
        options.signal.addEventListener('abort', () => reject(options.signal.reason), { once: true });
      });
    },
    async close() {},
  };
  const result = await evaluate(
    { arguments: {} },
    { contains: 'x' },
    {},
    {
      env: env('https://mcp.example.test/service', { LOTUSPOND_MCP_TIMEOUT_MS: '250' }),
      connectionFactory: () => ({ client, transport: {} }),
    },
  );
  assert.equal(result.passed, null);
  assert.equal(result.output.error, 'timeout');
});

test('rejects redirects instead of following a different destination', async () => {
  let redirectedHits = 0;
  const redirected = createServer((_request, response) => {
    redirectedHits += 1;
    response.writeHead(200, { 'content-type': 'application/json' }).end('{}');
  });
  servers.push(redirected);
  await new Promise((resolve) => redirected.listen(0, '127.0.0.1', resolve));
  const redirectedAddress = redirected.address();
  const mock = await mockMcpServer({
    responseHook: ({ response }) => {
      response
        .writeHead(302, { location: `http://127.0.0.1:${redirectedAddress.port}/other` })
        .end();
      return true;
    },
  });

  const result = await evaluate({ arguments: {} }, { contains: 'x' }, {}, { env: env(mock.endpoint) });
  assert.equal(result.passed, null);
  assert.equal(result.output.error, 'transport_error');
  assert.equal(redirectedHits, 0);
});

test('rejects a declared response larger than the configured bound', async () => {
  const mock = await mockMcpServer({
    responseHook: ({ response }) => {
      response.writeHead(200, { 'content-type': 'application/json', 'content-length': '4096' }).end('{}');
      return true;
    },
  });
  const result = await evaluate(
    { arguments: {} },
    { contains: 'x' },
    {},
    { env: env(mock.endpoint, { LOTUSPOND_MCP_MAX_RESPONSE_BYTES: '1024' }) },
  );
  assert.equal(result.passed, null);
  assert.equal(result.output.error, 'response_too_large');
});

test('stops reading a chunked response when it crosses the byte bound', async () => {
  const mock = await mockMcpServer({
    responseHook: ({ response }) => {
      response.writeHead(200, { 'content-type': 'application/json' });
      response.write('x'.repeat(2048));
      response.end();
      return true;
    },
  });
  const result = await evaluate(
    { arguments: {} },
    { contains: 'x' },
    {},
    { env: env(mock.endpoint, { LOTUSPOND_MCP_MAX_RESPONSE_BYTES: '1024' }) },
  );
  assert.equal(result.passed, null);
  assert.equal(result.output.error, 'response_too_large');
});

test('maps protected-endpoint authentication failure without exposing provider data', async () => {
  const mock = await mockMcpServer({
    responseHook: ({ response }) => {
      response
        .writeHead(401, {
          'content-type': 'application/json',
          'www-authenticate': 'Bearer realm="private"',
        })
        .end('{"private":"provider detail"}');
      return true;
    },
  });
  const result = await evaluate({ arguments: {} }, {}, {}, { env: env(mock.endpoint) });
  assert.equal(result.passed, null);
  assert.equal(result.output.error, 'authentication_failed');
  assert.doesNotMatch(JSON.stringify(result), /provider detail|secret-test-token/);
});

test('does not retain base64 audio or image payloads in output evidence', async () => {
  const mock = await mockMcpServer({
    callResult: {
      content: [
        { type: 'image', mimeType: 'image/png', data: 'aGVsbG8=' },
        { type: 'audio', mimeType: 'audio/wav', data: 'aGVsbG8=' },
      ],
    },
  });
  const result = await evaluate({ arguments: {} }, {}, {}, { env: env(mock.endpoint) });
  assert.equal(result.passed, null);
  assert.deepEqual(result.output.content, [
    { type: 'image', mimeType: 'image/png', bytes: 6 },
    { type: 'audio', mimeType: 'audio/wav', bytes: 6 },
  ]);
  assert.equal(result.evidence.outcome, 'output_incomplete');
  assert.deepEqual(result.evidence.outputIssues, ['content_summarized']);
  assert.doesNotMatch(JSON.stringify(result), /aGVsbG8/);
});

test('truncated text cannot pass from the retained prefix', async () => {
  const mock = await mockMcpServer({
    callResult: { content: [{ type: 'text', text: `matching prefix ${'x'.repeat(2000)}` }] },
  });
  const result = await evaluate(
    { arguments: {} },
    { contains: 'matching prefix' },
    {},
    { env: env(mock.endpoint, { LOTUSPOND_MCP_MAX_OUTPUT_BYTES: '1024' }) },
  );
  assert.equal(result.passed, null);
  assert.equal(result.evidence.outcome, 'output_incomplete');
  assert.equal(result.evidence.outputComplete, false);
  assert.ok(result.evidence.outputIssues.includes('content_truncated'));
  assert.match(result.output.content[0].text, /^matching prefix/);
});

test('omitted oversized structured content cannot pass from remaining text', async () => {
  const mock = await mockMcpServer({
    callResult: {
      content: [{ type: 'text', text: 'looks good' }],
      structuredContent: { payload: 'x'.repeat(2000) },
    },
  });
  const result = await evaluate(
    { arguments: {} },
    { contains: 'looks good' },
    {},
    { env: env(mock.endpoint, { LOTUSPOND_MCP_MAX_OUTPUT_BYTES: '1024' }) },
  );
  assert.equal(result.passed, null);
  assert.equal(result.evidence.outcome, 'output_incomplete');
  assert.ok(result.evidence.outputIssues.includes('structured_content_truncated'));
  assert.equal(Object.hasOwn(result.output, 'structuredContent'), false);
});

test('ambiguous, empty, and extended expectations remain unassessed', async () => {
  const expectations = [
    { contains: 'approved', equals: 'approved answer' },
    { contains: '' },
    { contains: 'approved', note: 'extra field' },
    { equals: Number.NaN },
  ];
  for (const expected of expectations) {
    const mock = await mockMcpServer();
    const result = await evaluate({ arguments: {} }, expected, {}, { env: env(mock.endpoint) });
    assert.equal(result.passed, null);
    assert.equal(result.evidence.outcome, 'unassessed');
    assert.equal(result.evidence.expectation, 'unsupported');
  }
});

test('structured exact comparison is deterministic across object key order', async () => {
  const mock = await mockMcpServer({
    callResult: { content: [], structuredContent: { alpha: 1, nested: { beta: 2 } } },
  });
  const result = await evaluate(
    { arguments: {} },
    { equals: { nested: { beta: 2 }, alpha: 1 } },
    {},
    { env: env(mock.endpoint) },
  );
  assert.equal(result.passed, true);
});

test('rejects non-finite arguments before network access', async () => {
  let constructed = false;
  const result = await evaluate(
    { arguments: { temperature: Number.NaN } },
    {},
    {},
    {
      env: env('https://mcp.example.test/service'),
      connectionFactory: () => {
        constructed = true;
      },
    },
  );
  assert.equal(result.passed, null);
  assert.equal(result.output.error, 'invalid_number');
  assert.equal(constructed, false);
});
