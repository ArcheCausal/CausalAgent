import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './tests/e2e-mock',
  timeout: 60_000,
  use: {
    baseURL: 'http://127.0.0.1:5174/dashboard-assets/',
    trace: 'retain-on-failure',
  },
})
