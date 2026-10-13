import { Agent as HttpAgent, request as httpRequest } from 'node:http';
import { Agent as HttpsAgent, request as httpsRequest } from 'node:https';
import { Readable } from 'node:stream';

export function createDirectAgent(secure) {
  return secure
    ? new HttpsAgent({ keepAlive: false, proxyEnv: {}, rejectUnauthorized: true })
    : new HttpAgent({ keepAlive: false, proxyEnv: {} });
}

export async function directFetch(target, { method, headers, body, signal, lookup } = {}) {
  const url = target instanceof URL ? target : new URL(target);
  const secure = url.protocol === 'https:';
  const agent = createDirectAgent(secure);
  try {
    return await new Promise((accept, reject) => {
      const request = (secure ? httpsRequest : httpRequest)(
        url,
        {
          method,
          agent,
          signal,
          rejectUnauthorized: true,
          maxHeaderSize: 16384,
          ...(lookup ? { lookup } : {}),
          headers: {
            ...headers,
            'accept-encoding': 'identity',
            ...(body === undefined ? {} : { 'content-length': Buffer.byteLength(body) }),
          },
        },
        (response) => {
          response.once('close', () => agent.destroy());
          accept({
            ok: response.statusCode >= 200 && response.statusCode < 300,
            status: response.statusCode,
            headers: {
              get(name) {
                const value = response.headers[name.toLowerCase()];
                return Array.isArray(value) ? value.join(', ') : (value ?? null);
              },
            },
            body: Readable.toWeb(response, {
              strategy: { highWaterMark: 65536, size: (chunk) => chunk.byteLength },
            }),
          });
        },
      );
      request.once('error', (error) => {
        agent.destroy();
        reject(error);
      });
      request.end(body);
    });
  } catch (error) {
    agent.destroy();
    throw error;
  }
}
