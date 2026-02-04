import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: 'playwright',
  use: {
    headless: true,
    viewport: { width: 1280, height: 720 }
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } }
  ]
})
