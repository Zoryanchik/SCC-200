import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/setupTests.js',
    include: [
      'src/**/*.{test,spec}.{js,jsx,ts,tsx}',
    ],
    // Ensure we only run our own unit tests
    exclude: [
      'node_modules/**',
      'dist/**',
      '.cache/**',
      '**/.cache/**',
      'cypress/**',
      'playwright/**'
    ]
  }
})
