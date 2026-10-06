import { defineConfig, devices } from '@playwright/test';

const external = process.env.E2E_BASE_URL;
export default defineConfig({
  testDir: './tests/e2e',
  fullyParallel: true,
  retries: 0,
  use: { baseURL: external || 'http://127.0.0.1:4173', trace: 'off' },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: external ? undefined : [
    { command: '../.venv/bin/python ../backend/tests/serve_e2e.py',
      url: 'http://127.0.0.1:8000/health/live', reuseExistingServer: false },
    { command: 'npm run dev -- --port 4173 --strictPort', url: 'http://127.0.0.1:4173', reuseExistingServer: false },
  ],
});
