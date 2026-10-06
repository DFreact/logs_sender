import { defineConfig } from 'vitest/config';
import { localizedPages } from './build/localized-pages.ts';

export default defineConfig({
  plugins: [localizedPages()],
  server: {
    proxy: {
      '/api/': 'http://127.0.0.1:8000',
      '/health/': 'http://127.0.0.1:8000',
    },
  },
  build: { sourcemap: false },
  test: { include: ['tests/unit/**/*.test.{ts,tsx}'] },
});
