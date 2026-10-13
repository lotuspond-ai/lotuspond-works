import { Agent as HttpAgent, request as httpRequest } from 'node:http';
import { Agent as HttpsAgent, request as httpsRequest } from 'node:https';
import { isIP } from 'node:net';
import { performance } from 'node:perf_hooks';

// Shared hardened POST transport for operator-configured agent endpoints. Every adapter
// that reaches a customer endpoint goes through this one path, so the guarantees live in
// a single place: pinned DNS resolution, no ambient proxy, TLS verification kept on,
// redirects refused, identity encoding only, and a bounded response read inside a
// whole-operation deadline.

class AdapterError extends Error {
  constructor(code) {
    super(code);
    this.code = code;
  }
}

function bound(env, name, fallback, maximum) {
  const raw = env[name];
  if (raw === undefined) return fallback;
  if (typeof raw !== 'string' || !/^\d+$/.test(raw))
    throw new AdapterError('invalid_configuration');
  const value = Number(raw);
  const minimum = name.endsWith('TIMEOUT_MS') ? 250 : 1024;
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum)
    throw new AdapterError('invalid_configuration');
  return value;
}

function loopback(address) {
  return address === '::1' || (isIP(address) === 4 && address.startsWith('127.'));
}

function operation(parent, timeoutMs) {
  const controller = new AbortController();
  const deadline = performance.now() + timeoutMs;
  const cancel = () => controller.abort(new AdapterError('cancelled'));
  const timer = setTimeout(() => controller.abort(new AdapterError('timeout')), timeoutMs);
  if (parent?.aborted) cancel();
  else parent?.addEventListener('abort', cancel, { once: true });
  return {
    signal: controller.signal,
    check() {
      controller.signal.throwIfAborted();
      if (performance.now() >= deadline) throw new AdapterError('timeout');
    },
    dispose() {
      clearTimeout(timer);
      parent?.removeEventListener('abort', cancel);
    },
  };
}

async function destination(config, signal, resolve) {
  signal.throwIfAborted();
  const addresses = isIP(config.hostname)
    ? [{ address: config.hostname, family: isIP(config.hostname) }]
    : await new Promise((accept, reject) => {
        const abort = () => reject(signal.reason);
        signal.addEventListener('abort', abort, { once: true });
        Promise.resolve()
          .then(() => resolve(config.hostname, { all: true, verbatim: true }))
          .then(accept, reject)
          .finally(() => signal.removeEventListener('abort', abort));
      });
  signal.throwIfAborted();
  if (!addresses.length || addresses.some((item) => !isIP(item.address)))
    throw new AdapterError('connection_failed');
  if (config.local && addresses.some((item) => !loopback(item.address)))
    throw new AdapterError('endpoint_rejected');
  // Pin the selected resolution for the connection; localhost must never resolve outside loopback.
  return addresses[0];
}

async function invoke(config, bytes, active, resolve) {
  const address = await destination(config, active.signal, resolve);
  active.check();
  const secure = config.url.protocol === 'https:';
  // Explicit agents do not inherit Node's global agent or ambient proxy variables.
  const agent = secure
    ? new HttpsAgent({ keepAlive: false, rejectUnauthorized: true, proxyEnv: {} })
    : new HttpAgent({ keepAlive: false, proxyEnv: {} });
  try {
    return await new Promise((accept, reject) => {
      const request = (secure ? httpsRequest : httpRequest)(
        config.url,
        {
          method: 'POST',
          agent,
          signal: active.signal,
          rejectUnauthorized: true,
          maxHeaderSize: 16384,
          lookup(_hostname, options, callback) {
            callback(null, options.all ? [address] : address.address, address.family);
          },
          headers: {
            accept: 'application/json, text/plain',
            'accept-encoding': 'identity',
            'content-type': 'application/json',
            'content-length': bytes.length,
            ...(config.token ? { authorization: `Bearer ${config.token}` } : {}),
          },
        },
        (response) => {
          const fail = (code) => {
            reject(new AdapterError(code));
            response.destroy();
            request.destroy();
          };
          response.on('error', () => reject(new AdapterError('response_incomplete')));
          const status = response.statusCode ?? 0;
          if (status >= 300 && status < 400) return fail('redirect_refused');
          if (status === 401 || status === 403) return fail('authentication_failed');
          if (status < 200 || status >= 300) return fail('http_error');
          if (
            response.headers['content-encoding'] &&
            response.headers['content-encoding'] !== 'identity'
          )
            return fail('unsupported_encoding');
          if (Number(response.headers['content-length']) > config.maxResponse)
            return fail('response_too_large');
          const chunks = [];
          let size = 0;
          response.on('data', (chunk) => {
            size += chunk.length;
            if (size > config.maxResponse) return fail('response_too_large');
            chunks.push(chunk);
          });
          response.on('end', () => {
            if (!response.complete) return fail('response_incomplete');
            accept({
              bytes: Buffer.concat(chunks),
              contentType: response.headers['content-type'] ?? '',
            });
          });
        },
      );
      request.on('error', () => reject(new AdapterError('connection_failed')));
      request.end(bytes);
    });
  } finally {
    agent.destroy();
  }
}

export { AdapterError, bound, loopback, operation, destination, invoke };
