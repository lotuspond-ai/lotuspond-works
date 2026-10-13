import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { afterEach, test } from 'node:test';
import { evaluate } from '../src/a2a-runner.mjs';

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

function completedTask(text, extra = {}) {
  return {
    kind: 'task',
    id: 'task-1',
    contextId: 'context-1',
    status: { state: 'completed' },
    artifacts: [{ parts: [{ kind: 'text', text }] }],
    ...extra,
  };
}

async function endpoint(result = completedTask('the approved answer')) {
  const requests = [];
  const server = createServer(async (request, response) => {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    requests.push({
      method: request.method,
      headers: request.headers,
      body: JSON.parse(Buffer.concat(chunks).toString('utf8')),
    });
    if (typeof result === 'function') return result(request, response);
    response
      .writeHead(200, { 'content-type': 'application/json' })
      .end(JSON.stringify({ jsonrpc: '2.0', id: requests.at(-1).body.id, result }));
  });
  servers.push(server);
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  return { url: `http://127.0.0.1:${server.address().port}/a2a`, requests };
}

async function run(url, extra = {}, expected = { contains: 'approved' }, input = { prompt: 'hi' }) {
  return evaluate(input, expected, {}, {
    env: { LOTUSPOND_A2A_ENDPOINT: url, LOTUSPOND_A2A_TIMEOUT_MS: '2000', ...extra },
  });
}

test('a completed task is judged against the expectation and sends valid JSON-RPC', async () => {
  const server = await endpoint();
  const token = 'synthetic-a2a-adapter-credential';
  const result = await run(server.url, { LOTUSPOND_A2A_TOKEN: token });
  assert.equal(result.passed, true);
  assert.equal(result.output.text, 'the approved answer');
  assert.equal(result.evidence.protocol, 'a2a');
  assert.equal(result.evidence.task_state, 'completed');
  assert.equal(result.evidence.outcome, 'assessed');
  // A2A reports no spend, so cost stays unavailable rather than a measured zero.
  assert.equal(result.evidence.cost_measurement, 'unavailable');
  assert.equal(result.costUsd, 0);

  const sent = server.requests[0];
  assert.equal(sent.method, 'POST');
  assert.equal(sent.headers.authorization, `Bearer ${token}`);
  assert.equal(sent.body.jsonrpc, '2.0');
  assert.equal(sent.body.method, 'message/send');
  assert.deepEqual(sent.body.params.message.parts, [{ kind: 'text', text: 'hi' }]);
  assert.equal(sent.body.params.message.role, 'user');
});

test('an unfinished or failed task is unknown rather than a pass or a failure', async () => {
  for (const [state, code] of [
    ['working', 'task_unfinished'],
    ['input-required', 'task_unfinished'],
    ['auth-required', 'task_unfinished'],
    ['failed', 'task_not_completed'],
    ['canceled', 'task_not_completed'],
    ['rejected', 'task_not_completed'],
  ]) {
    const server = await endpoint({
      kind: 'task',
      id: 'task-1',
      status: { state, message: { parts: [{ kind: 'text', text: 'approved' }] } },
    });
    const result = await run(server.url);
    assert.equal(result.passed, null, state);
    assert.equal(result.evidence.code, code, state);
    assert.equal(result.evidence.task_state, state, state);
  }
});

test('a direct agent message counts as an answer', async () => {
  const server = await endpoint({
    kind: 'message',
    role: 'agent',
    messageId: 'message-1',
    parts: [{ kind: 'text', text: 'an approved reply' }],
  });
  const result = await run(server.url);
  assert.equal(result.passed, true);
  assert.equal(result.output.text, 'an approved reply');
});

test('a JSON-RPC error keeps its code without leaking the agent message', async () => {
  const server = await endpoint((_request, response) => {
    response.writeHead(200, { 'content-type': 'application/json' }).end(
      JSON.stringify({
        jsonrpc: '2.0',
        id: '1',
        error: { code: -32001, message: 'internal detail that must not surface' },
      }),
    );
  });
  const result = await run(server.url);
  assert.equal(result.passed, null);
  assert.equal(result.evidence.code, 'agent_error');
  assert.equal(result.evidence.rpc_error_code, -32001);
  assert.equal(JSON.stringify(result).includes('internal detail'), false);
});

test('an expectation the adapter cannot judge stays unassessed instead of passing', async () => {
  const server = await endpoint();
  const result = await run(server.url, {}, { regex: 'approved' });
  assert.equal(result.passed, null);
  assert.equal(result.evidence.outcome, 'unassessed');
  assert.equal(result.evidence.expectation, 'unsupported');
  assert.equal(result.evidence.task_state, 'completed');
});

test('an empty answer is missing output, not an empty pass', async () => {
  const server = await endpoint(completedTask('   '));
  const result = await run(server.url, {}, { contains: '' });
  assert.equal(result.passed, null);
  assert.equal(result.evidence.code, 'output_missing');
});

test('the endpoint must be configured, remote endpoints must use TLS, and airgap refuses', async () => {
  assert.equal((await evaluate({ prompt: 'x' }, {}, {}, { env: {} })).evidence.code, 'configuration_missing');
  const plain = await evaluate({ prompt: 'x' }, {}, {}, {
    env: { LOTUSPOND_A2A_ENDPOINT: 'http://agent.example.com/a2a' },
  });
  assert.equal(plain.evidence.code, 'https_required');
  const credentialled = await evaluate({ prompt: 'x' }, {}, {}, {
    env: { LOTUSPOND_A2A_ENDPOINT: 'https://user:secret@agent.example.com/a2a' },
  });
  assert.equal(credentialled.evidence.code, 'endpoint_rejected');
  // A well-formed remote endpoint is still refused when the installation has no egress.
  const airgapped = await evaluate({ prompt: 'x' }, {}, {}, {
    env: {
      LOTUSPOND_A2A_ENDPOINT: 'https://agent.example.com/a2a',
      LOTUSPOND_NETWORK_MODE: 'airgap',
    },
  });
  assert.equal(airgapped.evidence.code, 'airgap');
  // Loopback stays reachable in airgap mode, which is how the offline drill exercises it.
  const server = await endpoint();
  const local = await run(server.url, { LOTUSPOND_NETWORK_MODE: 'airgap' });
  assert.equal(local.passed, true);
});

test('an operator credential is never echoed back as agent output', async () => {
  const token = 'synthetic-a2a-leaked-credential';
  const server = await endpoint(completedTask(`here is ${token}`));
  const result = await run(server.url, { LOTUSPOND_A2A_TOKEN: token }, { contains: 'here' });
  assert.equal(result.passed, null);
  assert.equal(result.evidence.code, 'sensitive_output');
  assert.equal(JSON.stringify(result).includes(token), false);
});
