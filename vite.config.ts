import { defineConfig } from 'vite';

export default defineConfig({
  root: 'web',
  build: { outDir: '../dist', emptyOutDir: true },
  server: { port: 3001, strictPort: true, proxy: { '/api': 'http://127.0.0.1:8000' } },
});
