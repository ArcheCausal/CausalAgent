import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'node:path'

export default defineConfig({
  base: '/',
  resolve: {
    alias: {
      '@causalagent/design-system': resolve(__dirname, '../packages/design-system/src/index.ts'),
    },
  },
  plugins: [vue()],
  test: {
    environment: 'jsdom',
    globals: true,
    include: ['./tests/**/*.spec.ts'],
    restoreMocks: true,
  },
})

