import { readFileSync } from 'node:fs';

import { defineConfig, type ProxyOptions } from 'vite';

// Browser writes are admitted only when Origin equals LOTUSPOND_ORIGIN. The dev
// server is that origin even when a port forward serves the page on another host.
function devOrigin(): string {
  try {
    const match = readFileSync(new URL('./.env', import.meta.url), 'utf8').match(
      /^LOTUSPOND_ORIGIN=(.+)$/m,
    );
    const value = match?.[1]?.trim().replace(/^["']|["']$/g, '');
    if (value) return value;
  } catch {
    // No local env file: the documented dev-server address.
  }
  return 'http://127.0.0.1:3001';
}

const apiProxy: ProxyOptions = {
  target: 'http://127.0.0.1:8000',
  configure(proxy) {
    const origin = devOrigin();
    proxy.on('proxyReq', (proxyReq) => {
      if (proxyReq.getHeader('origin')) proxyReq.setHeader('origin', origin);
    });
  },
};

export default defineConfig({
  root: 'web',
  build: { outDir: '../dist', emptyOutDir: true },
  server: { port: 3001, strictPort: true, proxy: { '/api': apiProxy } },
});
