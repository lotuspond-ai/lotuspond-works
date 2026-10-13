import WebSocket from 'ws';

import { createDirectAgent } from '../../scripts/direct-transport.mjs';

export function createDirectSocket(url, options = {}) {
  const target = new URL(url);
  const agent = createDirectAgent(target.protocol === 'wss:' || target.protocol === 'https:');
  const socket = new WebSocket(url, { ...options, agent, rejectUnauthorized: true });
  const destroy = () => agent.destroy();
  socket.once('close', destroy);
  socket.once('error', destroy);
  return socket;
}
