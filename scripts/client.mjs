import { lookup } from 'node:dns';
import { isIP } from 'node:net';
import { lotuspondApiBase } from './api-url.mjs';
import { directFetch } from './direct-transport.mjs';

const maxRequestBytes = 2 * 1024 * 1024;
const maxResponseBytes = 8 * 1024 * 1024;
const messages = {
  invalid_configuration: 'Configure a scoped Lotuspond key and a permitted API URL.',
  invalid_path: 'The Lotuspond request path is not permitted.',
  request_too_large: 'The Lotuspond request exceeds the 2 MiB limit.',
  response_too_large: 'The Lotuspond response exceeds the 8 MiB limit.',
  invalid_response: 'The Lotuspond API returned an invalid JSON response.',
  request_failed: 'The Lotuspond request failed.',
  unauthorized: 'The Lotuspond API key is missing, expired or revoked.',
  forbidden: 'The Lotuspond API denied this operation.',
  conflict: 'The Lotuspond operation conflicts with its current state.',
  aborted: 'The Lotuspond request was cancelled.',
  timeout: 'The Lotuspond request exceeded its deadline.',
};

export class ClientError extends Error {
  constructor(code, status = null) {
    super(messages[code] || messages.request_failed);
    this.name = 'ClientError';
    this.code = code;
    this.status = status;
  }
}

function verifiedLocalhost(_hostname, options, callback) {
  lookup('localhost', { all: true, verbatim: true }, (error, addresses) => {
    if (error) return callback(error);
    if (
      !addresses.length ||
      addresses.some(
        ({ address }) => address !== '::1' && !(isIP(address) === 4 && address.startsWith('127.')),
      )
    )
      return callback(new ClientError('invalid_configuration'));
    if (options.all) callback(null, addresses);
    else callback(null, addresses[0].address, addresses[0].family);
  });
}

function apiFetch(target, options) {
  return directFetch(target, {
    ...options,
    headers: { ...options.headers, accept: 'application/json' },
    lookup: target.hostname === 'localhost' ? verifiedLocalhost : undefined,
  });
}

export function createClient({ baseUrl, apiKey, fetchImpl = apiFetch }) {
  let base;
  try {
    base = new URL(lotuspondApiBase(baseUrl));
  } catch {
    throw new ClientError('invalid_configuration');
  }
  if (!apiKey || typeof apiKey !== 'string' || /[\r\n]/.test(apiKey))
    throw new ClientError('invalid_configuration');
  const prefix = base.pathname.replace(/\/$/, '');
  base.pathname = prefix;
  return async function request(path, body, { signal, timeoutMs = 30_000 } = {}) {
    if (!Number.isInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 30_000)
      throw new ClientError('invalid_configuration');
    let target;
    try {
      if (typeof path !== 'string' || !path.startsWith('/') || path.startsWith('//'))
        throw new Error();
      target = new URL(base.href.replace(/\/$/, '') + path);
      if (target.origin !== base.origin || !target.pathname.startsWith(prefix + '/') || target.hash)
        throw new Error();
    } catch {
      throw new ClientError('invalid_path');
    }
    let payload;
    try {
      payload = body === undefined ? undefined : JSON.stringify(body);
    } catch {
      throw new ClientError('request_failed');
    }
    if (payload !== undefined && Buffer.byteLength(payload) > maxRequestBytes)
      throw new ClientError('request_too_large');
    const timeout = AbortSignal.timeout(timeoutMs);
    const combined = signal ? AbortSignal.any([signal, timeout]) : timeout;
    let reader;
    try {
      combined.throwIfAborted();
      const response = await fetchImpl(target, {
        method: body === undefined ? 'GET' : 'POST',
        headers: {
          authorization: `Bearer ${apiKey}`,
          ...(body === undefined ? {} : { 'content-type': 'application/json' }),
        },
        body: payload,
        signal: combined,
        redirect: 'error',
      });
      if (!response.ok) {
        await response.body?.cancel();
        const code = { 401: 'unauthorized', 403: 'forbidden', 409: 'conflict' }[response.status];
        throw new ClientError(code || 'request_failed', response.status);
      }
      if (Number(response.headers.get('content-length')) > maxResponseBytes) {
        await response.body?.cancel();
        throw new ClientError('response_too_large');
      }
      const encoding = response.headers.get('content-encoding');
      if (encoding && encoding.toLowerCase() !== 'identity') {
        await response.body?.cancel();
        throw new ClientError('invalid_response');
      }
      if (!response.body) throw new ClientError('invalid_response');
      reader = response.body.getReader();
      const chunks = [];
      let received = 0;
      while (true) {
        combined.throwIfAborted();
        const { done, value } = await reader.read();
        if (done) break;
        received += value.byteLength;
        if (received > maxResponseBytes) throw new ClientError('response_too_large');
        chunks.push(value);
      }
      combined.throwIfAborted();
      try {
        const parsed = JSON.parse(
          new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(chunks)),
        );
        if (!parsed || typeof parsed !== 'object') throw new Error();
        return parsed;
      } catch {
        throw new ClientError('invalid_response');
      }
    } catch (error) {
      if (signal?.aborted) throw new ClientError('aborted');
      if (timeout.aborted) throw new ClientError('timeout');
      if (error instanceof ClientError) throw error;
      throw new ClientError('request_failed');
    } finally {
      await reader?.cancel().catch(() => {});
      reader?.releaseLock();
    }
  };
}

let defaultClient;
export async function request(path, body, options) {
  defaultClient ??= createClient({
    baseUrl: process.env.LOTUSPOND_API_URL || 'http://127.0.0.1:8000/api/v1',
    apiKey: process.env.LOTUSPOND_API_KEY,
  });
  return defaultClient(path, body, options);
}
