import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './tests/e2e-mock',
  timeout: 60_000,
  fullyParallel: false,
  webServer: {
    command: 'npm run dev',
    url: 'http://127.0.0.1:5174/dashboard-assets/',
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
  use: {
    ...devices['Desktop Chrome'],
    baseURL: 'http://127.0.0.1:5174/dashboard-assets/',
    trace: 'retain-on-failure',
  },
})
