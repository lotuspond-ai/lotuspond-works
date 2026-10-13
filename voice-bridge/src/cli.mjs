#!/usr/bin/env node
import process from 'node:process';

import { parseArgs } from './config.mjs';
import { BridgeError, runBridge } from './core.mjs';
import { createDirectSocket } from './socket.mjs';

try {
  const config = parseArgs(process.argv.slice(2));
  await runBridge(config, { createSocket: createDirectSocket });
  process.stderr.write('voice_bridge_completed\n');
} catch (error) {
  const code = error instanceof BridgeError ? error.code : 'bridge_failed';
  process.stderr.write(`voice_bridge_failed code=${code}\n`);
  process.exitCode = 1;
}
