import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  base: '/static/',
  server: {
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
  build: {
    outDir: '../ai_agent_platform/static',
    emptyOutDir: true,
    assetsDir: 'assets',
    manifest: true,
  },
  test: {
    environment: 'jsdom',
    restoreMocks: true,
    setupFiles: ['./tests/setup.ts'],
  },
});
