import {
  Client,
  InsufficientScopeError,
  SdkHttpError,
  StreamableHTTPClientTransport,
  UnauthorizedError,
} from '@modelcontextprotocol/client';

const DEFAULT_TIMEOUT_MS = 20_000;
const DEFAULT_MAX_RESPONSE_BYTES = 1_048_576;
const DEFAULT_MAX_OUTPUT_BYTES = 131_072;
const MAX_ARGUMENT_BYTES = 262_144;
const MAX_TOOLS = 200;

export class AdapterError extends Error {
  constructor(code) {
    super(code);
    this.name = 'AdapterError';
    this.code = code;
  }
}

function boundedInteger(value, fallback, minimum, maximum, name) {
  if (value === undefined || value === '') return fallback;
  if (!/^[0-9]+$/.test(value)) throw new AdapterError(`invalid_${name}`);
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed < minimum || parsed > maximum)
    throw new AdapterError(`invalid_${name}`);
  return parsed;
}

function isLoopback(hostname) {
  return hostname === 'localhost' || hostname === '127.0.0.1' || hostname === '[::1]';
}

function parseEndpoint(raw) {
  if (!raw) throw new AdapterError('configuration_missing');
  let endpoint;
  try {
    endpoint = new URL(raw);
  } catch {
    throw new AdapterError('invalid_endpoint');
  }
  if (endpoint.username || endpoint.password || endpoint.search || endpoint.hash)
    throw new AdapterError('invalid_endpoint');
  if (endpoint.protocol !== 'https:' && !(endpoint.protocol === 'http:' && isLoopback(endpoint.hostname)))
    throw new AdapterError('endpoint_not_allowed');
  return endpoint;
}

export function readConfig(env = process.env, { requireTool = true } = {}) {
  if (env.LOTUSPOND_NETWORK_MODE === 'airgap') throw new AdapterError('airgap');
  const endpoint = parseEndpoint(env.LOTUSPOND_MCP_ENDPOINT);
  const tool = env.LOTUSPOND_MCP_TOOL;
  if (requireTool && (!tool || !/^[A-Za-z0-9_.:/-]{1,128}$/.test(tool)))
    throw new AdapterError(tool ? 'invalid_tool' : 'configuration_missing');
  const token = env.LOTUSPOND_MCP_TOKEN;
  if (token !== undefined && (token.length < 1 || token.length > 8192))
    throw new AdapterError('invalid_token');
  return Object.freeze({
    endpoint,
    tool,
    token,
    timeoutMs: boundedInteger(env.LOTUSPOND_MCP_TIMEOUT_MS, DEFAULT_TIMEOUT_MS, 250, 60_000, 'timeout'),
    maxResponseBytes: boundedInteger(
      env.LOTUSPOND_MCP_MAX_RESPONSE_BYTES,
      DEFAULT_MAX_RESPONSE_BYTES,
      1024,
      4_194_304,
      'response_limit',
    ),
    maxOutputBytes: boundedInteger(
      env.LOTUSPOND_MCP_MAX_OUTPUT_BYTES,
      DEFAULT_MAX_OUTPUT_BYTES,
      1024,
      524_288,
      'output_limit',
    ),
  });
}

function exactDestination(input, endpoint) {
  const raw = typeof input === 'string' || input instanceof URL ? input : input.url;
  let destination;
  try {
    destination = new URL(raw);
  } catch {
    throw new AdapterError('destination_not_allowed');
  }
  if (destination.href !== endpoint.href) throw new AdapterError('destination_not_allowed');
}

export function createBoundedFetch(config, fetchImpl = globalThis.fetch) {
  return async (input, init = {}) => {
    exactDestination(input, config.endpoint);
    const timeout = AbortSignal.timeout(config.timeoutMs);
    const signals = [timeout];
    if (init.signal) signals.push(init.signal);
    let response;
    try {
      response = await fetchImpl(input, {
        ...init,
        redirect: 'error',
        signal: signals.length === 1 ? timeout : AbortSignal.any(signals),
      });
    } catch (error) {
      if (timeout.aborted) throw new AdapterError('timeout');
      throw error;
    }
    const declaredLength = Number(response.headers.get('content-length'));
    if (Number.isFinite(declaredLength) && declaredLength > config.maxResponseBytes) {
      await response.body?.cancel();
      throw new AdapterError('response_too_large');
    }
    if (!response.body) return response;
    let received = 0;
    const boundedBody = response.body.pipeThrough(
      new TransformStream({
        transform(chunk, controller) {
          received += chunk.byteLength;
          if (received > config.maxResponseBytes) {
            controller.error(new AdapterError('response_too_large'));
            return;
          }
          controller.enqueue(chunk);
        },
      }),
    );
    return new Response(boundedBody, {
      status: response.status,
      statusText: response.statusText,
      headers: response.headers,
    });
  };
}

export function createConnection(config, { fetchImpl = globalThis.fetch } = {}) {
  const client = new Client({ name: 'lotuspond-protocol-adapter', version: '0.1.0' });
  const transport = new StreamableHTTPClientTransport(config.endpoint, {
    authProvider: config.token ? { token: async () => config.token } : undefined,
    fetch: createBoundedFetch(config, fetchImpl),
    onInsufficientScope: 'throw',
    requestInit: { redirect: 'error' },
  });
  return { client, transport };
}

export function operationSignal(externalSignal, timeoutMs) {
  const timeoutController = new AbortController();
  const timer = setTimeout(
    () => timeoutController.abort(new DOMException('Operation timed out', 'TimeoutError')),
    timeoutMs,
  );
  const timeout = timeoutController.signal;
  return {
    signal: externalSignal ? AbortSignal.any([externalSignal, timeout]) : timeout,
    externalSignal,
    timeout,
    dispose() {
      clearTimeout(timer);
    },
  };
}

export function publicErrorCode(error, operation) {
  if (operation.externalSignal?.aborted) return 'cancelled';
  if (operation.timeout.aborted) return 'timeout';
  if (error instanceof AdapterError) return error.code;
  if (error instanceof UnauthorizedError) return 'authentication_failed';
  if (error instanceof InsufficientScopeError) return 'authorization_failed';
  if (error instanceof SdkHttpError) {
    if (error.status === 401) return 'authentication_failed';
    if (error.status === 403) return 'authorization_failed';
    return 'http_error';
  }
  if (error?.name === 'AbortError') return 'cancelled';
  if (error?.code === 'RequestTimeout' || error?.name === 'TimeoutError') return 'timeout';
  return 'transport_error';
}

export async function closeQuietly(client) {
  if (!client?.close) return;
  let timer;
  try {
    await Promise.race([
      client.close(),
      new Promise((resolve) => {
        timer = setTimeout(resolve, 1000);
      }),
    ]);
  } catch {
    // Closing must not overwrite the operation result or expose transport details.
  } finally {
    clearTimeout(timer);
  }
}

function validateJson(value, depth = 0, seen = new Set()) {
  if (depth > 16) throw new AdapterError('input_too_deep');
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return;
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) throw new AdapterError('invalid_number');
    return;
  }
  if (typeof value !== 'object' || seen.has(value)) throw new AdapterError('invalid_input');
  seen.add(value);
  if (Array.isArray(value)) {
    for (const item of value) validateJson(item, depth + 1, seen);
  } else {
    const prototype = Object.getPrototypeOf(value);
    if (prototype !== Object.prototype && prototype !== null) throw new AdapterError('invalid_input');
    for (const [key, item] of Object.entries(value)) {
      if (key === '__proto__' || key === 'constructor' || key === 'prototype')
        throw new AdapterError('invalid_input');
      validateJson(item, depth + 1, seen);
    }
  }
  seen.delete(value);
}

export function parseArguments(input) {
  if (!input || typeof input !== 'object' || Array.isArray(input)) throw new AdapterError('invalid_input');
  if (Object.keys(input).some((key) => key !== 'arguments')) throw new AdapterError('invalid_input');
  const args = input.arguments ?? {};
  if (!args || typeof args !== 'object' || Array.isArray(args)) throw new AdapterError('invalid_input');
  validateJson(args);
  if (Buffer.byteLength(JSON.stringify(args)) > MAX_ARGUMENT_BYTES) throw new AdapterError('input_too_large');
  return args;
}

function safeContentBlock(block) {
  if (!block || typeof block !== 'object') return { block: { type: 'unsupported' }, complete: false };
  if (block.type === 'text' && typeof block.text === 'string') {
    return { block: { type: 'text', text: block.text }, complete: true };
  }
  if (block.type === 'image' || block.type === 'audio') {
    const bytes = typeof block.data === 'string' ? Math.floor((block.data.length * 3) / 4) : 0;
    return {
      block: { type: block.type, mimeType: String(block.mimeType ?? '').slice(0, 128), bytes },
      complete: false,
    };
  }
  if (block.type === 'resource_link') {
    return {
      block: {
        type: 'resource_link',
        name: String(block.name ?? '').slice(0, 256),
        title: String(block.title ?? '').slice(0, 256),
        uri: String(block.uri ?? '').slice(0, 2048),
        mimeType: String(block.mimeType ?? '').slice(0, 128),
      },
      complete: false,
    };
  }
  return {
    block: { type: String(block.type ?? 'unsupported').slice(0, 64) },
    complete: false,
  };
}

function fitTextBlock(text, byteLimit) {
  const characters = Array.from(text);
  let low = 0;
  let high = characters.length;
  while (low < high) {
    const middle = Math.ceil((low + high) / 2);
    const candidate = { type: 'text', text: characters.slice(0, middle).join('') };
    if (Buffer.byteLength(JSON.stringify(candidate)) <= byteLimit) low = middle;
    else high = middle - 1;
  }
  return { type: 'text', text: characters.slice(0, low).join('') };
}

export function normalizeToolResult(result, maxOutputBytes) {
  const content = [];
  const issues = [];
  let remaining = Math.max(0, maxOutputBytes - 128);
  const blocks = Array.isArray(result?.content) ? result.content : [];
  if (!Array.isArray(result?.content)) issues.push('invalid_result');
  if (blocks.length > 50) issues.push('content_truncated');
  for (const source of blocks.slice(0, 50)) {
    const normalized = safeContentBlock(source);
    if (!normalized.complete && !issues.includes('content_summarized')) issues.push('content_summarized');
    let block = normalized.block;
    let size = Buffer.byteLength(JSON.stringify(block)) + 1;
    if (size > remaining && block.type === 'text') {
      block = fitTextBlock(block.text, Math.max(0, remaining - 1));
      size = Buffer.byteLength(JSON.stringify(block)) + 1;
      if (!issues.includes('content_truncated')) issues.push('content_truncated');
    }
    if (size > remaining) {
      if (!issues.includes('content_truncated')) issues.push('content_truncated');
      break;
    }
    remaining -= size;
    content.push(block);
    if (block.type === 'text' && block.text !== source.text) break;
  }
  const output = { content };
  if (result?.structuredContent !== undefined) {
    try {
      validateJson(result.structuredContent);
      const serialized = JSON.stringify(result.structuredContent);
      if (Buffer.byteLength(serialized) + 32 <= remaining) output.structuredContent = result.structuredContent;
      else issues.push('structured_content_truncated');
    } catch {
      issues.push('invalid_structured_content');
    }
  }
  return { output, complete: issues.length === 0, issues };
}

export function sanitizeTools(tools) {
  return (Array.isArray(tools) ? tools : []).slice(0, MAX_TOOLS).map((tool) => ({
    name: String(tool?.name ?? '').slice(0, 128),
    title: tool?.title ? String(tool.title).slice(0, 256) : undefined,
    description: tool?.description ? String(tool.description).slice(0, 1024) : undefined,
  }));
}

export function requestOptions(operation, timeoutMs) {
  return { signal: operation.signal, timeout: timeoutMs, maxTotalTimeout: timeoutMs };
}
