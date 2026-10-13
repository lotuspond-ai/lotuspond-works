#!/usr/bin/env node
import { pathToFileURL } from 'node:url';
import {
  AdapterError,
  closeQuietly,
  createConnection,
  operationSignal,
  publicErrorCode,
  readConfig,
  requestOptions,
  sanitizeTools,
} from './mcp-client.mjs';

export async function listAdvertisedTools(options = {}) {
  let config;
  try {
    config = readConfig(options.env ?? process.env, { requireTool: false });
  } catch (error) {
    return { status: 'unknown', code: error instanceof AdapterError ? error.code : 'configuration_missing', tools: [] };
  }
  const operation = operationSignal(options.signal, config.timeoutMs);
  let client;
  try {
    const connection = options.connectionFactory
      ? options.connectionFactory(config)
      : createConnection(config, { fetchImpl: options.fetchImpl });
    client = connection.client;
    await client.connect(connection.transport, requestOptions(operation, config.timeoutMs));
    const result = await client.listTools(undefined, {
      ...requestOptions(operation, config.timeoutMs),
      cacheMode: 'bypass',
    });
    return { status: 'advertised', tools: sanitizeTools(result?.tools) };
  } catch (error) {
    return { status: 'unknown', code: publicErrorCode(error, operation), tools: [] };
  } finally {
    operation.dispose();
    await closeQuietly(client);
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const result = await listAdvertisedTools();
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
  process.exitCode = result.status === 'advertised' ? 0 : 1;
}
