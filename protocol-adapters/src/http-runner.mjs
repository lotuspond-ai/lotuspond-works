import { lookup } from 'node:dns/promises';
import { isIP } from 'node:net';
import {
  AdapterError,
  bound,
  destination,
  invoke,
  loopback,
  operation,
} from './http-transport.mjs';

function responsePath(raw) {
  if (raw === undefined || raw === '') return null;
  if (typeof raw !== 'string' || raw.length > 256) throw new AdapterError('invalid_configuration');
  const normalized = raw.replace(/\[(\d+)\]/g, '.$1');
  const parts = normalized.split('.');
  if (
    parts.length > 16 ||
    parts.some(
      (part) =>
        !/^[A-Za-z0-9_-]+$/.test(part) || ['__proto__', 'prototype', 'constructor'].includes(part),
    )
  )
    throw new AdapterError('invalid_configuration');
  return parts;
}

function configuration(env) {
  if (!env.LOTUSPOND_HTTP_ENDPOINT) throw new AdapterError('configuration_missing');
  if (typeof env.LOTUSPOND_HTTP_ENDPOINT !== 'string' || env.LOTUSPOND_HTTP_ENDPOINT.length > 2048)
    throw new AdapterError('invalid_configuration');
  let url;
  try {
    url = new URL(env.LOTUSPOND_HTTP_ENDPOINT);
  } catch {
    throw new AdapterError('invalid_configuration');
  }
  const hostname = url.hostname.replace(/^\[|\]$/g, '');
  const local = loopback(hostname) || hostname === 'localhost';
  if (
    url.username ||
    url.password ||
    url.search ||
    url.hash ||
    !['http:', 'https:'].includes(url.protocol)
  )
    throw new AdapterError('endpoint_rejected');
  if (url.protocol === 'http:' && !local) throw new AdapterError('https_required');
  const networkMode = env.LOTUSPOND_NETWORK_MODE ?? 'connected';
  if (!['connected', 'airgap'].includes(networkMode))
    throw new AdapterError('invalid_configuration');
  if (networkMode === 'airgap' && !local) throw new AdapterError('airgap');
  const mode = env.LOTUSPOND_HTTP_MODE ?? 'plain-input';
  if (!['plain-input', 'openai-chat'].includes(mode))
    throw new AdapterError('invalid_configuration');
  const token = env.LOTUSPOND_HTTP_TOKEN;
  if (token !== undefined && (typeof token !== 'string' || !/^[\x21-\x7e]{1,4096}$/.test(token)))
    throw new AdapterError('invalid_configuration');
  const model = env.LOTUSPOND_HTTP_MODEL;
  const system = env.LOTUSPOND_HTTP_SYSTEM_PROMPT;
  if (model !== undefined && (typeof model !== 'string' || !model.trim() || model.length > 256))
    throw new AdapterError('invalid_configuration');
  if (
    system !== undefined &&
    (typeof system !== 'string' || !system.trim() || Buffer.byteLength(system) > 32768)
  )
    throw new AdapterError('invalid_configuration');
  return {
    url,
    hostname,
    local,
    mode,
    token,
    model,
    system,
    path: responsePath(env.LOTUSPOND_HTTP_RESPONSE_PATH),
    timeoutMs: bound(env, 'LOTUSPOND_HTTP_TIMEOUT_MS', 20000, 60000),
    maxInput: bound(env, 'LOTUSPOND_HTTP_MAX_INPUT_BYTES', 65536, 262144),
    maxResponse: bound(env, 'LOTUSPOND_HTTP_MAX_RESPONSE_BYTES', 1048576, 2097152),
    maxOutput: bound(env, 'LOTUSPOND_HTTP_MAX_OUTPUT_BYTES', 65536, 262144),
  };
}

function requestBody(input, config) {
  if (
    !input ||
    typeof input !== 'object' ||
    Array.isArray(input) ||
    Object.keys(input).length !== 1 ||
    typeof input.prompt !== 'string' ||
    !input.prompt.trim()
  )
    throw new AdapterError('invalid_input');
  if (Buffer.byteLength(input.prompt) > config.maxInput) throw new AdapterError('input_too_large');
  const body =
    config.mode === 'plain-input'
      ? { input: input.prompt }
      : {
          ...(config.model ? { model: config.model } : {}),
          messages: [
            ...(config.system ? [{ role: 'system', content: config.system }] : []),
            { role: 'user', content: input.prompt },
          ],
        };
  const bytes = Buffer.from(JSON.stringify(body));
  if (bytes.length > config.maxInput) throw new AdapterError('input_too_large');
  return bytes;
}

function atPath(value, parts) {
  for (const part of parts) {
    if (!value || typeof value !== 'object' || !Object.hasOwn(value, part)) return undefined;
    value = value[part];
  }
  return value;
}

function outputText(response, config) {
  let raw;
  try {
    raw = new TextDecoder('utf-8', { fatal: true }).decode(response.bytes);
  } catch {
    throw new AdapterError('invalid_response');
  }
  const mime = response.contentType.split(';')[0].trim().toLowerCase();
  let value;
  if (mime === 'application/json' || /^application\/[a-z0-9.+-]+\+json$/.test(mime)) {
    try {
      value = JSON.parse(raw);
    } catch {
      throw new AdapterError('invalid_response');
    }
  } else if (mime === 'text/plain') value = raw;
  else throw new AdapterError('unsupported_content_type');
  const candidates = config.path
    ? [atPath(value, config.path)]
    : [
        value,
        ...['output_text', 'text', 'output', 'reply', 'answer', 'response', 'content'].map((key) =>
          atPath(value, [key]),
        ),
        atPath(value, ['message', 'content']),
        atPath(value, ['choices', '0', 'message', 'content']),
        atPath(value, ['choices', '0', 'text']),
      ];
  const text = candidates.find((candidate) => typeof candidate === 'string' && candidate.trim());
  if (text === undefined) throw new AdapterError('output_missing');
  if (Buffer.byteLength(JSON.stringify({ text })) > config.maxOutput)
    throw new AdapterError('output_too_large');
  if (config.token && text.includes(config.token)) throw new AdapterError('sensitive_output');
  return text;
}

function verdict(text, expected) {
  if (
    !expected ||
    typeof expected !== 'object' ||
    Array.isArray(expected) ||
    Object.keys(expected).length !== 1
  )
    return null;
  const key = Object.keys(expected)[0];
  if (
    !['contains', 'equals'].includes(key) ||
    typeof expected[key] !== 'string' ||
    !expected[key].trim() ||
    Buffer.byteLength(expected[key]) > 262144
  )
    return null;
  return key === 'contains' ? text.includes(expected[key]) : text === expected[key];
}

function unknown(code) {
  return {
    passed: null,
    output: { error: code },
    evidence: {
      protocol: 'http',
      outcome: 'unknown',
      code,
      outputComplete: false,
      cost_measurement: 'unavailable',
    },
  };
}

export async function evaluate(input, expected, context = {}, dependencies = {}) {
  let active;
  try {
    const config = configuration(dependencies.env ?? process.env);
    const bytes = requestBody(input, config);
    active = operation(context.signal, config.timeoutMs);
    const response = await invoke(config, bytes, active, dependencies.lookup ?? lookup);
    active.check();
    const text = outputText(response, config);
    const passed = verdict(text, expected);
    active.check();
    return {
      passed,
      output: { text },
      evidence: {
        protocol: 'http',
        requestMode: config.mode,
        outcome: passed === null ? 'unassessed' : 'assessed',
        expectation: passed === null ? 'unsupported' : 'supported',
        outputComplete: true,
        cost_measurement: 'unavailable',
      },
    };
  } catch (error) {
    const reason = active?.signal.aborted ? active.signal.reason : error;
    return unknown(reason instanceof AdapterError ? reason.code : 'connection_failed');
  } finally {
    active?.dispose();
  }
}
