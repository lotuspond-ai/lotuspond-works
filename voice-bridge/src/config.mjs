import fs from 'node:fs';

import { lotuspondApiBase } from '../../scripts/api-url.mjs';
import { BridgeError } from './core.mjs';

function integer(value, name, minimum, maximum) {
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed < minimum || parsed > maximum)
    throw new BridgeError('invalid_configuration', `${name} is invalid`);
  return parsed;
}

function endpoint(value, schemes, name) {
  let url;
  try {
    url = new URL(value);
  } catch {
    throw new BridgeError('invalid_configuration', `${name} is invalid`);
  }
  if (
    !schemes.includes(url.protocol) ||
    url.username ||
    url.password ||
    url.search ||
    url.hash
  )
    throw new BridgeError('invalid_configuration', `${name} is invalid`);
  return url.toString().replace(/\/$/, '');
}

export function parseChannelMap(value, channels) {
  const result = new Map();
  for (const item of String(value || '').split(',')) {
    const match = /^(\d+):(user|agent|other)$/.exec(item);
    if (!match) throw new BridgeError('invalid_channel_map', 'Every channel needs an explicit role');
    const channel = Number(match[1]);
    if (channel >= channels || result.has(channel))
      throw new BridgeError('invalid_channel_map', 'Every channel needs one explicit role');
    result.set(channel, match[2]);
  }
  if (result.size !== channels)
    throw new BridgeError('invalid_channel_map', 'Every channel needs one explicit role');
  return result;
}

export function parseArgs(argv, env = process.env) {
  const args = new Map();
  for (let index = 0; index < argv.length; index += 2) {
    if (!argv[index]?.startsWith('--') || argv[index + 1] === undefined)
      throw new BridgeError('invalid_arguments', 'Arguments must use --name value pairs');
    args.set(argv[index].slice(2), argv[index + 1]);
  }
  const networkMode = env.LOTUSPOND_NETWORK_MODE || 'connected';
  if (!['connected', 'airgap'].includes(networkMode))
    throw new BridgeError('invalid_configuration', 'LOTUSPOND_NETWORK_MODE is invalid');
  if (networkMode === 'airgap')
    throw new BridgeError('network_disabled', 'Voice provider traffic is disabled in airgap mode');
  const channels = integer(args.get('channels') || '1', 'channels', 1, 2);
  const sessionId = args.get('session');
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(sessionId || ''))
    throw new BridgeError('invalid_configuration', 'session is invalid');
  const sourceKind = args.get('source') || 'stdin';
  if (!['stdin', 'file', 'device'].includes(sourceKind))
    throw new BridgeError('invalid_configuration', 'source is invalid');
  const sourcePath = args.get('path');
  if (sourceKind !== 'stdin' && !sourcePath)
    throw new BridgeError('invalid_configuration', 'file and device sources require --path');
  if (!env.DEEPGRAM_API_KEY || !env.LOTUSPOND_API_KEY)
    throw new BridgeError('credentials_missing', 'Provider and Lotuspond server keys are required');
  let lotuspondEndpoint;
  try {
    lotuspondEndpoint = lotuspondApiBase(env.LOTUSPOND_API_URL || 'http://127.0.0.1:8000');
  } catch {
    throw new BridgeError('invalid_configuration', 'LOTUSPOND_API_URL is invalid');
  }
  return {
    networkMode,
    sessionId,
    sourceKind,
    sourcePath,
    source: sourceKind === 'stdin' ? process.stdin : fs.createReadStream(sourcePath, { highWaterMark: 32_000 }),
    channels,
    channelMap: parseChannelMap(args.get('channel-map'), channels),
    sampleRate: integer(args.get('sample-rate') || '16000', 'sample-rate', 8000, 48000),
    encoding: 'linear16',
    model: args.get('model') || 'nova-3',
    providerEndpoint: endpoint(
      env.DEEPGRAM_ENDPOINT || 'wss://api.deepgram.com/v1/listen',
      ['wss:'],
      'DEEPGRAM_ENDPOINT',
    ),
    providerKey: env.DEEPGRAM_API_KEY,
    lotuspondEndpoint,
    lotuspondKey: env.LOTUSPOND_API_KEY,
    audioChunkBytes: 32_000,
    maxBufferedBytes: 256_000,
    eventQueueSize: 64,
    connectTimeoutMs: 10_000,
    providerIdleTimeoutMs: 30_000,
    backpressureTimeoutMs: 5_000,
    ingestTimeoutMs: 5_000,
    finalizeWaitMs: 1_000,
  };
}
