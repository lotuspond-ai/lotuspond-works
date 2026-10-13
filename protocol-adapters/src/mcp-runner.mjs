import {
  AdapterError,
  closeQuietly,
  createConnection,
  normalizeToolResult,
  operationSignal,
  parseArguments,
  publicErrorCode,
  readConfig,
  requestOptions,
} from './mcp-client.mjs';

function canonicalJson(value) {
  if (Array.isArray(value)) return value.map(canonicalJson);
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, item]) => [key, canonicalJson(item)]),
    );
  }
  return value;
}

function isJsonValue(value, depth = 0, seen = new Set()) {
  if (depth > 16) return false;
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return true;
  if (typeof value === 'number') return Number.isFinite(value);
  if (typeof value !== 'object' || seen.has(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  if (!Array.isArray(value) && prototype !== Object.prototype && prototype !== null) return false;
  seen.add(value);
  const valid = Array.isArray(value)
    ? value.every((item) => isJsonValue(item, depth + 1, seen))
    : Object.entries(value).every(
        ([key, item]) =>
          key !== '__proto__' &&
          key !== 'constructor' &&
          key !== 'prototype' &&
          isJsonValue(item, depth + 1, seen),
      );
  seen.delete(value);
  return valid;
}

function expectedVerdict(output, expected) {
  if (!expected || typeof expected !== 'object' || Array.isArray(expected)) return null;
  const keys = Object.keys(expected);
  if (keys.length !== 1) return null;
  const text = output.content
    .filter((block) => block.type === 'text')
    .map((block) => block.text)
    .join('\n');
  if (keys[0] === 'contains' && typeof expected.contains === 'string' && expected.contains.length > 0)
    return text.includes(expected.contains);
  if (keys[0] === 'equals' && isJsonValue(expected.equals)) {
    const actual = Object.hasOwn(output, 'structuredContent') ? output.structuredContent : text;
    try {
      return JSON.stringify(canonicalJson(actual)) === JSON.stringify(canonicalJson(expected.equals));
    } catch {
      return null;
    }
  }
  return null;
}

function unknown(code, outcome = 'unknown') {
  return {
    passed: null,
    output: { error: code },
    costUsd: 0,
    evidence: {
      protocol: 'mcp',
      transport: 'streamable-http',
      outcome,
      code,
      cost_measurement: 'unavailable',
    },
  };
}

export async function evaluate(input, expected, context = {}, dependencies = {}) {
  let config;
  let argumentsValue;
  try {
    config = readConfig(dependencies.env ?? process.env);
    argumentsValue = parseArguments(input);
  } catch (error) {
    return unknown(error instanceof AdapterError ? error.code : 'invalid_input');
  }

  const operation = operationSignal(context.signal, config.timeoutMs);
  let client;
  try {
    const connection = dependencies.connectionFactory
      ? dependencies.connectionFactory(config)
      : createConnection(config, { fetchImpl: dependencies.fetchImpl });
    client = connection.client;
    await client.connect(connection.transport, requestOptions(operation, config.timeoutMs));
    const result = await client.callTool(
      { name: config.tool, arguments: argumentsValue },
      requestOptions(operation, config.timeoutMs),
    );
    const normalized = normalizeToolResult(result, config.maxOutputBytes);
    const { output } = normalized;
    if (result?.isError === true) {
      return {
        passed: null,
        output,
        costUsd: 0,
        evidence: {
          protocol: 'mcp',
          transport: 'streamable-http',
          tool: config.tool,
          outcome: 'tool_error',
          outputComplete: normalized.complete,
          outputIssues: normalized.issues,
          cost_measurement: 'unavailable',
        },
      };
    }
    if (!normalized.complete) {
      return {
        passed: null,
        output,
        costUsd: 0,
        evidence: {
          protocol: 'mcp',
          transport: 'streamable-http',
          tool: config.tool,
          outcome: 'output_incomplete',
          outputComplete: false,
          outputIssues: normalized.issues,
          cost_measurement: 'unavailable',
        },
      };
    }
    const passed = expectedVerdict(output, expected);
    return {
      passed,
      output,
      costUsd: 0,
      evidence: {
        protocol: 'mcp',
        transport: 'streamable-http',
        tool: config.tool,
        outcome: passed === null ? 'unassessed' : 'assessed',
        expectation: passed === null ? 'unsupported' : 'supported',
        outputComplete: true,
        cost_measurement: 'unavailable',
      },
    };
  } catch (error) {
    return unknown(publicErrorCode(error, operation));
  } finally {
    operation.dispose();
    await closeQuietly(client);
  }
}
