import { lookup } from 'node:dns/promises';
import { randomUUID } from 'node:crypto';
import {
  AdapterError,
  bound,
  destination,
  invoke,
  loopback,
  operation,
} from './http-transport.mjs';

// A2A carries JSON-RPC 2.0 over a single POST endpoint, so it reuses the same hardened
// transport as the HTTP adapter rather than opening a second way out to a customer host.
const PROTOCOL = 'a2a';
const METHOD = 'message/send';
// Only a completed task has produced an answer to judge. Every other state is a fact
// about the conversation, not a result, and must stay unknown rather than become a pass.
const TERMINAL_STATES = new Set(['completed', 'failed', 'canceled', 'cancelled', 'rejected']);

function configuration(env) {
  if (!env.LOTUSPOND_A2A_ENDPOINT) throw new AdapterError('configuration_missing');
  if (typeof env.LOTUSPOND_A2A_ENDPOINT !== 'string' || env.LOTUSPOND_A2A_ENDPOINT.length > 2048)
    throw new AdapterError('invalid_configuration');
  let url;
  try {
    url = new URL(env.LOTUSPOND_A2A_ENDPOINT);
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
  const token = env.LOTUSPOND_A2A_TOKEN;
  if (token !== undefined && (typeof token !== 'string' || !/^[\x21-\x7e]{1,4096}$/.test(token)))
    throw new AdapterError('invalid_configuration');
  return {
    url,
    hostname,
    local,
    token,
    timeoutMs: bound(env, 'LOTUSPOND_A2A_TIMEOUT_MS', 30_000, 120_000),
    maxInput: bound(env, 'LOTUSPOND_A2A_MAX_INPUT_BYTES', 256 * 1024, 1024 * 1024),
    maxResponse: bound(env, 'LOTUSPOND_A2A_MAX_RESPONSE_BYTES', 1024 * 1024, 4 * 1024 * 1024),
    maxOutput: bound(env, 'LOTUSPOND_A2A_MAX_OUTPUT_BYTES', 64 * 1024, 1024 * 1024),
  };
}

function promptOf(input) {
  if (typeof input === 'string') return input;
  if (input && typeof input === 'object' && !Array.isArray(input)) {
    for (const key of ['prompt', 'input', 'text', 'message']) {
      if (typeof input[key] === 'string' && input[key].trim()) return input[key];
    }
  }
  throw new AdapterError('invalid_input');
}

function requestBytes(config, prompt) {
  const body = {
    jsonrpc: '2.0',
    id: randomUUID(),
    method: METHOD,
    params: {
      message: {
        kind: 'message',
        role: 'user',
        messageId: randomUUID(),
        parts: [{ kind: 'text', text: prompt }],
      },
    },
  };
  const bytes = Buffer.from(JSON.stringify(body));
  if (bytes.length > config.maxInput) throw new AdapterError('input_too_large');
  return bytes;
}

function decode(response) {
  let raw;
  try {
    raw = new TextDecoder('utf-8', { fatal: true }).decode(response.bytes);
  } catch {
    throw new AdapterError('invalid_response');
  }
  const mime = response.contentType.split(';')[0].trim().toLowerCase();
  if (mime !== 'application/json' && !/^application\/[a-z0-9.+-]+\+json$/.test(mime))
    throw new AdapterError('unsupported_content_type');
  try {
    return JSON.parse(raw);
  } catch {
    throw new AdapterError('invalid_response');
  }
}

function textParts(value, collected, depth = 0) {
  if (depth > 8 || !value) return collected;
  const parts = Array.isArray(value?.parts) ? value.parts : [];
  for (const part of parts) {
    if (part && part.kind === 'text' && typeof part.text === 'string') collected.push(part.text);
  }
  return collected;
}

function answerOf(result) {
  // A direct Message reply, or a Task whose artifacts carry the answer. A task's status
  // message is read only as a fallback, because it often explains rather than answers.
  const collected = [];
  if (result?.kind === 'message' || Array.isArray(result?.parts)) textParts(result, collected);
  for (const artifact of Array.isArray(result?.artifacts) ? result.artifacts : [])
    textParts(artifact, collected);
  if (!collected.length) textParts(result?.status?.message, collected);
  return collected.join('\n');
}

function stateOf(result) {
  if (result?.kind === 'message' || (Array.isArray(result?.parts) && !result?.status))
    return 'completed';
  const state = result?.status?.state;
  return typeof state === 'string' ? state.toLowerCase() : null;
}

function verdict(text, expected) {
  if (
    !expected ||
    typeof expected !== 'object' ||
    Array.isArray(expected) ||
    Object.keys(expected).length !== 1
  )
    return null;
  if (typeof expected.contains === 'string' && expected.contains.length > 0)
    return text.includes(expected.contains);
  if (typeof expected.equals === 'string') return text.trim() === expected.equals.trim();
  return null;
}

function unknown(code, extra = {}) {
  return {
    passed: null,
    output: { error: code },
    costUsd: 0,
    evidence: {
      protocol: PROTOCOL,
      method: METHOD,
      outcome: 'unknown',
      code,
      // A2A carries no billing measurement, so cost is unavailable rather than zero.
      cost_measurement: 'unavailable',
      ...extra,
    },
  };
}

export async function evaluate(input, expected, context = {}, dependencies = {}) {
  let config;
  let prompt;
  try {
    config = configuration(dependencies.env ?? process.env);
    prompt = promptOf(input);
  } catch (error) {
    return unknown(error instanceof AdapterError ? error.code : 'invalid_configuration');
  }
  const active = operation(context.signal, config.timeoutMs);
  try {
    const bytes = requestBytes(config, prompt);
    const response = await invoke(
      config,
      bytes,
      active,
      dependencies.resolve ?? ((hostname, options) => lookup(hostname, options)),
    );
    active.check();
    const envelope = decode(response);
    if (envelope?.error) {
      const code = Number(envelope.error?.code);
      return unknown('agent_error', {
        rpc_error_code: Number.isFinite(code) ? code : null,
      });
    }
    const result = envelope?.result;
    if (!result || typeof result !== 'object') return unknown('invalid_response');
    const state = stateOf(result);
    if (state !== 'completed')
      return unknown(TERMINAL_STATES.has(state) ? 'task_not_completed' : 'task_unfinished', {
        task_state: state,
      });
    const text = answerOf(result);
    if (!text.trim()) return unknown('output_missing', { task_state: state });
    if (Buffer.byteLength(text) > config.maxOutput)
      return unknown('output_too_large', { task_state: state });
    if (config.token && text.includes(config.token))
      return unknown('sensitive_output', { task_state: state });
    const passed = verdict(text, expected);
    return {
      passed,
      output: { text },
      costUsd: 0,
      evidence: {
        protocol: PROTOCOL,
        method: METHOD,
        task_state: state,
        outcome: passed === null ? 'unassessed' : 'assessed',
        expectation: passed === null ? 'unsupported' : 'supported',
        cost_measurement: 'unavailable',
      },
    };
  } catch (error) {
    const code = error instanceof AdapterError ? error.code : 'connection_failed';
    return unknown(code === 'cancelled' ? 'cancelled' : code);
  } finally {
    active.dispose();
  }
}

export default evaluate;
