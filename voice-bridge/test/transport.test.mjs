import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { mkdtemp, rm } from 'node:fs/promises';
import { createServer } from 'node:http';
import { createServer as createHttpsServer } from 'node:https';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { promisify } from 'node:util';

const executeFile = promisify(execFile);
const coreUrl = new URL('../src/core.mjs', import.meta.url).href;
const socketUrl = new URL('../src/socket.mjs', import.meta.url).href;
const sessionId = '12345678-1234-4234-8234-123456789abc';

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
  return { server, origin: `http://127.0.0.1:${server.address().port}` };
}

test('voice ingest uses a direct transport even when Node starts with ambient proxy support', async (t) => {
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
    authorized = request.headers.authorization === 'Bearer synthetic-ingest-key';
    response.writeHead(200).end('{}');
  });
  const source = `import {createIngest} from ${JSON.stringify(coreUrl)};
    const ingest=createIngest({
      lotuspondEndpoint:${JSON.stringify(target.origin + '/api/v1')},
      sessionId:${JSON.stringify(sessionId)},
      lotuspondKey:process.env.LOTUSPOND_TEST_KEY,
      ingestTimeoutMs:2000,
    });
    await ingest({sequence:0,speaker:'user',text:'hello',start_ms:0,end_ms:1,is_final:true});`;
  const { stdout, stderr } = await executeFile(
    process.execPath,
    ['--use-env-proxy', '--input-type=module', '-e', source],
    {
      env: {
        HTTP_PROXY: proxy.origin,
        HTTPS_PROXY: proxy.origin,
        ALL_PROXY: proxy.origin,
        NO_PROXY: '',
        NODE_USE_ENV_PROXY: '1',
        LOTUSPOND_TEST_KEY: 'synthetic-ingest-key',
      },
      timeout: 5000,
      maxBuffer: 4096,
    },
  );
  assert.equal(targetHits, 1);
  assert.equal(authorized, true);
  assert.equal(proxyHits, 0);
  assert.equal(
    stdout.includes('synthetic-ingest-key') || stderr.includes('synthetic-ingest-key'),
    false,
  );
});

test('voice ingest and provider sockets keep TLS verification when ambient TLS checks are disabled', async (t) => {
  try {
    await executeFile('openssl', ['version'], { timeout: 3000 });
  } catch {
    return t.skip('openssl is required for the synthetic TLS probe');
  }
  const directory = await mkdtemp(join(tmpdir(), 'lotuspond-voice-tls-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const key = join(directory, 'key.pem');
  const cert = join(directory, 'cert.pem');
  await executeFile(
    'openssl',
    [
      'req',
      '-x509',
      '-newkey',
      'rsa:2048',
      '-keyout',
      key,
      '-out',
      cert,
      '-days',
      '1',
      '-nodes',
      '-subj',
      '/CN=127.0.0.1',
      '-addext',
      'subjectAltName=IP:127.0.0.1',
    ],
    { timeout: 5000 },
  );
  const certificate = { key: readFileSync(key), cert: readFileSync(cert) };
  let ingestAuthorized = false;
  const ingestServer = createHttpsServer(certificate, (request, response) => {
    ingestAuthorized = request.headers.authorization === 'Bearer synthetic-ingest-key';
    response.writeHead(200).end('{}');
  });
  await new Promise((resolve) => ingestServer.listen(0, '127.0.0.1', resolve));
  t.after(
    () =>
      new Promise((resolve) => {
        ingestServer.closeAllConnections();
        ingestServer.close(resolve);
      }),
  );
  let socketAuthorized = false;
  const providerServer = createHttpsServer(certificate, () => {});
  providerServer.on('upgrade', (request, socket) => {
    socketAuthorized = request.headers.authorization === 'Token provider-secret';
    socket.end();
  });
  await new Promise((resolve) => providerServer.listen(0, '127.0.0.1', resolve));
  t.after(
    () =>
      new Promise((resolve) => {
        providerServer.closeAllConnections();
        providerServer.close(resolve);
      }),
  );
  const ingestPort = ingestServer.address().port;
  const providerPort = providerServer.address().port;
  const source = `
    import {createIngest} from ${JSON.stringify(coreUrl)};
    import {createDirectSocket} from ${JSON.stringify(socketUrl)};
    const ingest=createIngest({
      lotuspondEndpoint:'https://127.0.0.1:${ingestPort}/api/v1',
      sessionId:${JSON.stringify(sessionId)},
      lotuspondKey:process.env.LOTUSPOND_TEST_KEY,
      ingestTimeoutMs:2000,
    });
    let ingestFailed=false;
    try {
      await ingest({sequence:0,speaker:'user',text:'hello',start_ms:0,end_ms:1,is_final:true});
    } catch { ingestFailed=true; }
    let socketFailed=false;
    try {
      await new Promise((resolve, reject) => {
        const socket=createDirectSocket('wss://127.0.0.1:${providerPort}/v1/listen', {
          headers:{Authorization:'Token provider-secret'},
          handshakeTimeout:2000,
        });
        socket.once('open', () => reject(new Error('untrusted socket opened')));
        socket.once('error', resolve);
      });
      socketFailed=true;
    } catch { socketFailed=true; }
    if (!ingestFailed || !socketFailed) process.exitCode=1;`;
  await executeFile(process.execPath, ['--input-type=module', '-e', source], {
    env: {
      ...process.env,
      NODE_TLS_REJECT_UNAUTHORIZED: '0',
      LOTUSPOND_TEST_KEY: 'synthetic-ingest-key',
    },
    timeout: 8000,
    maxBuffer: 4096,
  });
  assert.equal(ingestAuthorized, false);
  assert.equal(socketAuthorized, false);
});
