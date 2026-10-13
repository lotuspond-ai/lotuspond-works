import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { Readable } from 'node:stream';
import test from 'node:test';

import { parseArgs, parseChannelMap } from '../src/config.mjs';
import {
  BoundedQueue,
  BridgeError,
  createIngest,
  createTranscriptProjector,
  deepgramUrl,
  runBridge,
} from '../src/core.mjs';

const sessionId = '12345678-1234-4234-8234-123456789abc';

function configuration(overrides = {}) {
  return {
    networkMode: 'connected',
    sessionId,
    source: Readable.from([Buffer.from([0, 1, 2, 3])]),
    channels: 2,
    channelMap: new Map([
      [0, 'user'],
      [1, 'agent'],
    ]),
    sampleRate: 16000,
    encoding: 'linear16',
    model: 'nova-3',
    providerEndpoint: 'wss://api.deepgram.com/v1/listen',
    providerKey: 'provider-secret',
    lotuspondEndpoint: 'https://lotuspond.example/api/v1',
    lotuspondKey: 'lotuspond-secret',
    audioChunkBytes: 2,
    maxBufferedBytes: 100,
    eventQueueSize: 4,
    connectTimeoutMs: 100,
    providerIdleTimeoutMs: 1000,
    backpressureTimeoutMs: 20,
    ingestTimeoutMs: 100,
    finalizeWaitMs: 1,
    ...overrides,
  };
}

class MockSocket extends EventEmitter {
  constructor(onSend = () => {}) {
    super();
    this.readyState = 0;
    this.bufferedAmount = 0;
    this.sent = [];
    this.onSend = onSend;
  }
  open() {
    this.readyState = 1;
    this.emit('open');
  }
  send(data, callback) {
    this.sent.push(data);
    this.onSend(data, this);
    callback?.();
  }
  close() {
    if (this.readyState === 3) return;
    this.readyState = 3;
    this.emit('close');
  }
}

function results({ transcript, final, channel = 0, start = 0, duration = 0.5 }) {
  return JSON.stringify({
    type: 'Results',
    channel_index: [channel, 2],
    start,
    duration,
    is_final: final,
    channel: { alternatives: [{ transcript }] },
  });
}

test('builds the documented Deepgram linear16 multichannel URL', () => {
  const url = new URL(deepgramUrl(configuration()));
  assert.equal(url.origin, 'wss://api.deepgram.com');
  assert.equal(url.pathname, '/v1/listen');
  assert.equal(url.searchParams.get('encoding'), 'linear16');
  assert.equal(url.searchParams.get('sample_rate'), '16000');
  assert.equal(url.searchParams.get('channels'), '2');
  assert.equal(url.searchParams.get('multichannel'), 'true');
  assert.equal(url.searchParams.get('interim_results'), 'true');
});

test('requires an explicit role for every audio channel', () => {
  assert.deepEqual([...parseChannelMap('0:user,1:agent', 2)], [
    [0, 'user'],
    [1, 'agent'],
  ]);
  assert.throws(() => parseChannelMap('0:user', 2), { code: 'invalid_channel_map' });
  assert.throws(() => parseChannelMap('0:user,0:agent', 2), { code: 'invalid_channel_map' });
});

test('projects interim revisions and one immutable final with provider timings', () => {
  const project = createTranscriptProjector(new Map([[0, 'user']]));
  assert.deepEqual(project(JSON.parse(results({ transcript: 'hel', final: false }))), {
    sequence: 0,
    speaker: 'user',
    text: 'hel',
    start_ms: 0,
    end_ms: 500,
    is_final: false,
  });
  const final = project(JSON.parse(results({ transcript: 'hello', final: true })));
  assert.equal(final.sequence, 0);
  assert.equal(final.is_final, true);
  assert.equal(project(JSON.parse(results({ transcript: 'changed', final: false }))), null);
});

test('posts transcript JSON with the server-side Lotuspond key', async () => {
  let request;
  const ingest = createIngest(configuration(), async (url, options) => {
    request = { url, options };
    return { ok: true };
  });
  await ingest({ sequence: 0, speaker: 'user', text: 'hello', start_ms: 0, end_ms: 1, is_final: true });
  assert.equal(request.url, `https://lotuspond.example/api/v1/voice/sessions/${sessionId}/segments`);
  assert.equal(request.options.headers.authorization, 'Bearer lotuspond-secret');
  assert.equal(JSON.parse(request.options.body).text, 'hello');
});

test('streams bounded PCM chunks and ingests partial and final events', async () => {
  const ingested = [];
  let socket;
  let options;
  const createSocket = (_url, supplied) => {
    options = supplied;
    socket = new MockSocket((data, target) => {
      if (typeof data === 'string' && JSON.parse(data).type === 'Finalize') {
        target.emit('message', results({ transcript: 'hel', final: false }));
        target.emit('message', results({ transcript: 'hello', final: true }));
      }
      if (typeof data === 'string' && JSON.parse(data).type === 'CloseStream')
        setImmediate(() => target.close());
    });
    queueMicrotask(() => socket.open());
    return socket;
  };
  await runBridge(configuration({ channels: 1, channelMap: new Map([[0, 'user']]) }), {
    createSocket,
    ingest: async (segment) => ingested.push(segment),
  });
  assert.equal(options.headers.Authorization, 'Token provider-secret');
  assert.equal(options.perMessageDeflate, false);
  assert.equal(options.maxPayload, 256000);
  assert.deepEqual(socket.sent.filter(Buffer.isBuffer).map((item) => item.length), [2, 2]);
  assert.deepEqual(ingested.map((item) => item.is_final), [false, true]);
  assert.equal(ingested[0].sequence, ingested[1].sequence);
});

test('provider output or ingest failure never fabricates a final segment', async () => {
  const attempted = [];
  const createSocket = () => {
    const socket = new MockSocket((data, target) => {
      if (typeof data === 'string' && JSON.parse(data).type === 'Finalize')
        target.emit('message', results({ transcript: 'partial only', final: false }));
    });
    queueMicrotask(() => socket.open());
    return socket;
  };
  await assert.rejects(
    runBridge(configuration({ channels: 1, channelMap: new Map([[0, 'user']]) }), {
      createSocket,
      ingest: async (segment) => {
        attempted.push(segment);
        throw new BridgeError('ingest_unavailable', 'unavailable');
      },
    }),
    { code: 'ingest_unavailable' },
  );
  assert.deepEqual(attempted.map((item) => item.is_final), [false]);
});

test('airgap refuses before opening a socket', async () => {
  let called = false;
  await assert.rejects(
    runBridge(configuration({ networkMode: 'airgap' }), {
      createSocket: () => {
        called = true;
      },
      ingest: async () => {},
    }),
    { code: 'network_disabled' },
  );
  assert.equal(called, false);
});

test('bounded event queue rejects overflow instead of dropping a final', () => {
  const queue = new BoundedQueue(1);
  assert.equal(queue.push('partial'), true);
  assert.equal(queue.push('final'), false);
});

test('sustained provider backpressure terminates without ingesting evidence', async () => {
  let ingested = false;
  const createSocket = () => {
    const socket = new MockSocket();
    socket.bufferedAmount = 1_000;
    queueMicrotask(() => socket.open());
    return socket;
  };
  await assert.rejects(
    runBridge(configuration({ maxBufferedBytes: 10, backpressureTimeoutMs: 2 }), {
      createSocket,
      ingest: async () => {
        ingested = true;
      },
    }),
    { code: 'provider_backpressure_timeout' },
  );
  assert.equal(ingested, false);
});

test('CLI configuration refuses missing credentials and remote plaintext API', () => {
  const argv = ['--session', sessionId, '--channel-map', '0:user'];
  assert.throws(() => parseArgs(argv, {}), { code: 'credentials_missing' });
  assert.throws(
    () =>
      parseArgs(argv, {
        DEEPGRAM_API_KEY: 'x',
        LOTUSPOND_API_KEY: 'y',
        LOTUSPOND_API_URL: 'http://example.com',
      }),
    { code: 'invalid_configuration' },
  );
  assert.throws(
    () =>
      parseArgs(argv, {
        LOTUSPOND_NETWORK_MODE: 'typo',
        DEEPGRAM_API_KEY: 'x',
        LOTUSPOND_API_KEY: 'y',
      }),
    { code: 'invalid_configuration' },
  );
});

test('CLI API URL accepts origin-only and /api/v1 forms', () => {
  const argv = ['--session', sessionId, '--channel-map', '0:user'];
  const credentials = { DEEPGRAM_API_KEY: 'x', LOTUSPOND_API_KEY: 'y' };
  assert.equal(
    parseArgs(argv, { ...credentials, LOTUSPOND_API_URL: 'http://127.0.0.1:8000' })
      .lotuspondEndpoint,
    'http://127.0.0.1:8000/api/v1',
  );
  assert.equal(
    parseArgs(argv, { ...credentials, LOTUSPOND_API_URL: 'http://127.0.0.1:8000/api/v1' })
      .lotuspondEndpoint,
    'http://127.0.0.1:8000/api/v1',
  );
  assert.equal(parseArgs(argv, credentials).lotuspondEndpoint, 'http://127.0.0.1:8000/api/v1');
});
