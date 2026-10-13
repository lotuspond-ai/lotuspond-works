import { setTimeout as delay } from 'node:timers/promises';

import { directFetch } from '../../scripts/direct-transport.mjs';

export class BridgeError extends Error {
  constructor(code, message) {
    super(message);
    this.name = 'BridgeError';
    this.code = code;
  }
}

export class BoundedQueue {
  #items = [];
  #waiters = [];
  #closed = false;
  constructor(capacity) {
    this.capacity = capacity;
  }
  push(value) {
    if (this.#closed) return false;
    const waiter = this.#waiters.shift();
    if (waiter) waiter({ value, done: false });
    else if (this.#items.length >= this.capacity) return false;
    else this.#items.push(value);
    return true;
  }
  close() {
    this.#closed = true;
    for (const waiter of this.#waiters.splice(0)) waiter({ done: true });
  }
  async next() {
    if (this.#items.length) return { value: this.#items.shift(), done: false };
    if (this.#closed) return { done: true };
    return new Promise((resolve) => this.#waiters.push(resolve));
  }
  [Symbol.asyncIterator]() {
    return this;
  }
}

export function deepgramUrl(config) {
  const url = new URL(config.providerEndpoint);
  const parameters = {
    model: config.model,
    encoding: config.encoding,
    sample_rate: String(config.sampleRate),
    channels: String(config.channels),
    multichannel: String(config.channels > 1),
    interim_results: 'true',
    smart_format: 'true',
    endpointing: '300',
  };
  for (const [key, value] of Object.entries(parameters)) url.searchParams.set(key, value);
  return url.toString();
}

export function createTranscriptProjector(channelMap) {
  let nextSequence = 0;
  const sequences = new Map();
  const finalized = new Set();
  return (event) => {
    if (event?.type !== 'Results') return null;
    const channel = event.channel_index?.[0];
    const speaker = channelMap.get(channel);
    const transcript = event.channel?.alternatives?.[0]?.transcript;
    if (!speaker || typeof transcript !== 'string' || !transcript.trim()) return null;
    const start = Number(event.start);
    const duration = Number(event.duration);
    if (!Number.isFinite(start) || !Number.isFinite(duration) || start < 0 || duration < 0)
      throw new BridgeError('provider_event_invalid', 'Provider timing was invalid');
    const startMs = Math.round(start * 1000);
    const key = `${channel}:${startMs}`;
    if (finalized.has(key)) return null;
    if (!sequences.has(key)) sequences.set(key, nextSequence++);
    const isFinal = event.is_final === true;
    if (typeof event.is_final !== 'boolean')
      throw new BridgeError('provider_event_invalid', 'Provider finality was invalid');
    if (isFinal) finalized.add(key);
    return {
      sequence: sequences.get(key),
      speaker,
      text: transcript,
      start_ms: startMs,
      end_ms: Math.round((start + duration) * 1000),
      is_final: isFinal,
    };
  };
}

export function createIngest(config, fetchImpl = directFetch) {
  const base = config.lotuspondEndpoint.replace(/\/$/, '');
  const url = `${base}/voice/sessions/${config.sessionId}/segments`;
  return async (segment) => {
    const signal = AbortSignal.timeout(config.ingestTimeoutMs);
    let response;
    try {
      response = await fetchImpl(url, {
        method: 'POST',
        headers: {
          authorization: `Bearer ${config.lotuspondKey}`,
          'content-type': 'application/json',
        },
        body: JSON.stringify(segment),
        signal,
      });
    } catch {
      throw new BridgeError('ingest_unavailable', 'Lotuspond transcript ingest was unavailable');
    }
    if (!response.ok)
      throw new BridgeError('ingest_rejected', 'Lotuspond rejected a transcript segment');
  };
}

function waitForOpen(socket, timeoutMs) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(
      () => reject(new BridgeError('provider_connect_timeout', 'Provider connection timed out')),
      timeoutMs,
    );
    socket.once('open', () => {
      clearTimeout(timer);
      resolve();
    });
    socket.once('error', () => {
      clearTimeout(timer);
      reject(new BridgeError('provider_unavailable', 'Provider connection failed'));
    });
  });
}

function send(socket, data) {
  return new Promise((resolve, reject) => {
    socket.send(data, (error) =>
      error
        ? reject(new BridgeError('provider_unavailable', 'Provider send failed'))
        : resolve(),
    );
  });
}

async function sendAudio(socket, chunk, config, fatalPromise, onProgress) {
  const bytes = Buffer.from(chunk);
  for (let offset = 0; offset < bytes.length; offset += config.audioChunkBytes) {
    const deadline = Date.now() + config.backpressureTimeoutMs;
    while (socket.bufferedAmount > config.maxBufferedBytes) {
      if (Date.now() >= deadline)
        throw new BridgeError('provider_backpressure_timeout', 'Provider send buffer stayed full');
      await Promise.race([delay(10), fatalPromise]);
    }
    await Promise.race([
      send(socket, bytes.subarray(offset, offset + config.audioChunkBytes)),
      fatalPromise,
    ]);
    onProgress();
  }
}

export async function runBridge(config, { createSocket, ingest = createIngest(config) }) {
  if (config.networkMode === 'airgap')
    throw new BridgeError('network_disabled', 'Voice provider traffic is disabled in airgap mode');
  if (!config.providerKey || !config.lotuspondKey)
    throw new BridgeError('credentials_missing', 'Provider and Lotuspond server keys are required');

  const socket = createSocket(deepgramUrl(config), {
    headers: { Authorization: `Token ${config.providerKey}` },
    handshakeTimeout: config.connectTimeoutMs,
    perMessageDeflate: false,
    maxPayload: 256_000,
  });
  await waitForOpen(socket, config.connectTimeoutMs);
  const queue = new BoundedQueue(config.eventQueueSize);
  const project = createTranscriptProjector(config.channelMap);
  let expectedClose = false;
  let fatalError;
  let rejectFatal;
  const fatalPromise = new Promise((_, reject) => {
    rejectFatal = reject;
  });
  fatalPromise.catch(() => {});
  const fail = (error) => {
    if (fatalError) return;
    fatalError = error instanceof BridgeError ? error : new BridgeError('bridge_failed', 'Bridge failed');
    rejectFatal(fatalError);
    queue.close();
  };
  let idleTimer;
  const resetIdle = () => {
    clearTimeout(idleTimer);
    idleTimer = setTimeout(
      () => fail(new BridgeError('provider_idle_timeout', 'Provider stopped responding')),
      config.providerIdleTimeoutMs,
    );
  };
  socket.on('message', (data) => {
    resetIdle();
    if (!queue.push(data)) fail(new BridgeError('provider_queue_full', 'Provider event queue filled'));
  });
  socket.on('error', () => fail(new BridgeError('provider_unavailable', 'Provider connection failed')));
  socket.on('close', () => {
    queue.close();
    if (!expectedClose) fail(new BridgeError('provider_closed', 'Provider closed unexpectedly'));
  });
  resetIdle();

  const consumer = (async () => {
    try {
      for await (const raw of queue) {
        let event;
        try {
          event = JSON.parse(Buffer.from(raw).toString('utf8'));
        } catch {
          throw new BridgeError('provider_event_invalid', 'Provider sent invalid JSON');
        }
        const segment = project(event);
        if (segment) await ingest(segment);
      }
    } catch (error) {
      fail(error);
    }
  })();

  try {
    const iterator = config.source[Symbol.asyncIterator]();
    for (;;) {
      const item = await Promise.race([iterator.next(), fatalPromise]);
      if (item.done) break;
      await sendAudio(socket, item.value, config, fatalPromise, resetIdle);
    }
    await Promise.race([send(socket, JSON.stringify({ type: 'Finalize' })), fatalPromise]);
    await Promise.race([delay(config.finalizeWaitMs), fatalPromise]);
    expectedClose = true;
    const closed = new Promise((resolve) => socket.once('close', resolve));
    await send(socket, JSON.stringify({ type: 'CloseStream' }));
    await Promise.race([
      closed,
      delay(500).then(() => socket.close()),
    ]);
    queue.close();
    await consumer;
    if (fatalError) throw fatalError;
  } finally {
    clearTimeout(idleTimer);
    queue.close();
    if (socket.readyState === 0 || socket.readyState === 1) socket.close();
  }
}
