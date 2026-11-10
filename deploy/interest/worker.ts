export { ConfirmationEmail } from './confirmation';

interface Env {
  ASSETS: { fetch(request: Request): Promise<Response> };
  INTEREST: { put(key: string, value: string): Promise<void> };
  SIGNUP_LIMIT: { limit(input: { key: string }): Promise<{ success: boolean }> };
  CONFIRMATIONS?: {
    idFromName(name: string): unknown;
    get(id: unknown): { fetch(request: Request): Promise<Response> };
  };
}

const origins = new Set(['https://lotuspond.ai', 'https://www.lotuspond.ai']);
const headers = {
  'Cache-Control': 'no-store',
  'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'no-referrer',
  'X-Frame-Options': 'DENY',
  'Permissions-Policy': 'camera=(), microphone=(), geolocation=()',
  'Content-Security-Policy':
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'; form-action 'self'",
};

function json(status: number, message: string) {
  return Response.json({ message }, { status, headers });
}

async function boundedBody(request: Request): Promise<string> {
  const reader = request.body?.getReader();
  if (!reader) throw new Error('empty');
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > 2048) {
        await reader.cancel();
        throw new Error('large');
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.length;
  }
  return new TextDecoder('utf-8', { fatal: true }).decode(bytes);
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname === '/api/interest') {
      if (request.method !== 'POST') return json(405, 'Use the registration form.');
      // Local development is accepted only when the Worker itself is on loopback.
      const local = ['localhost', '127.0.0.1'].includes(url.hostname);
      const origin = request.headers.get('Origin') ?? '';
      if (!(origins.has(origin) || (local && origin === url.origin))) {
        return json(403, 'Please register from lotuspond.ai.');
      }
      if (request.headers.get('Content-Type')?.split(';')[0].trim() !== 'application/json') {
        return json(415, 'Please use the registration form.');
      }
      let data: unknown;
      try {
        data = JSON.parse(await boundedBody(request));
      } catch {
        return json(400, 'Please check your email and try again.');
      }
      if (!data || typeof data !== 'object' || Array.isArray(data))
        return json(400, 'Invalid form.');
      const fields = data as Record<string, unknown>;
      const email = typeof fields.email === 'string' ? fields.email.trim().toLowerCase() : '';
      if (
        Object.keys(fields).some((key) => !['email', 'consent', 'website'].includes(key)) ||
        fields.consent !== true ||
        typeof fields.website !== 'string' ||
        fields.website !== '' ||
        email.length > 254 ||
        !/^[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$/i.test(
          email,
        ) ||
        email.split('@')[0].length > 64 ||
        email.split('@')[0].endsWith('.') ||
        email.startsWith('.') ||
        email.includes('..')
      ) {
        return json(400, 'Enter a valid email and agree to receive early access updates.');
      }
      try {
        const ip = request.headers.get('CF-Connecting-IP');
        if (!ip && !local)
          return json(503, 'Registration is temporarily unavailable. Please try again.');
        const allowed = await env.SIGNUP_LIMIT.limit({ key: `interest:${ip ?? 'local'}` });
        if (!allowed.success)
          return json(429, 'Too many attempts. Please wait a minute and try again.');
        const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(email));
        const key = Array.from(new Uint8Array(digest), (byte) =>
          byte.toString(16).padStart(2, '0'),
        ).join('');
        // One record per normalized address. Re-registering renews the consent timestamp.
        await env.INTEREST.put(
          `email:${key}`,
          JSON.stringify({
            email,
            consent: 'lotuspond-early-access-v1',
            registeredAt: new Date().toISOString(),
            source: 'lotuspond.ai',
          }),
        );
        if (env.CONFIRMATIONS) {
          try {
            const object = env.CONFIRMATIONS.get(env.CONFIRMATIONS.idFromName(key));
            const queued = await object.fetch(
              new Request('https://confirmation.internal/register', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email, key }),
              }),
            );
            if (!queued.ok) throw new Error('Confirmation unavailable');
            const confirmation = (await queued.json()) as { status: string };
            if (['failed', 'unknown'].includes(confirmation.status)) {
              return json(
                200,
                'Your interest is registered. We couldn’t confirm email delivery, but your place on the list is saved.',
              );
            }
            if (confirmation.status === 'accepted') {
              return json(
                200,
                'Your interest is already registered. Please check your inbox for your confirmation.',
              );
            }
            return json(200, 'Your interest is registered. Your confirmation email is on its way.');
          } catch {
            return json(
              200,
              'Your interest is registered. Your confirmation email is delayed, but your place on the list is saved.',
            );
          }
        }
        return json(200, 'You’re on the early access list. We’ll email you when invitations open.');
      } catch {
        // Never return a success before durable storage acknowledges the write.
        return json(503, 'We couldn’t save your email. Please try again shortly.');
      }
    }
    if (!['GET', 'HEAD'].includes(request.method)) return json(405, 'Method not allowed.');
    if (
      ![
        '/',
        '/index.html',
        '/style.css',
        '/signup.js',
        '/favicon.svg',
        '/robots.txt',
        '/vitruvian-man.svg',
        '/social/lotuspond-early-access-20261106-v1.jpg',
        '/social/lotuspond-early-access-20261106-v2.jpg',
      ].includes(url.pathname)
    ) {
      return new Response('Page not found', { status: 404, headers });
    }
    const response = await env.ASSETS.fetch(request);
    const secured = new Response(response.body, response);
    for (const [name, value] of Object.entries(headers)) secured.headers.set(name, value);
    return secured;
  },
};
