import { defineConfig, devices } from '@playwright/test';

// R4 smoke runs against a LIVE stack (frontend + backend WITH data), not a CI
// runner with an empty DB. Start the stack first:
//     docker compose up -d
// then from frontend/:
//     npm run e2e            (installs the browser once via e2e:install)
// Point it at another deployment with E2E_BASE_URL.
const baseURL = process.env.E2E_BASE_URL ?? 'http://localhost:5173';

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL,
    headless: true,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
