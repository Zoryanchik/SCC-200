import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/setupTests.js',
    // Ensure we only run our own unit tests
    exclude: [
      'node_modules/**',
      'dist/**',
      'cypress/**',
      'playwright/**'
    ]
  }
})
