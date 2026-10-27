import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

// 交互回归直接使用实际 reducer、Store、组件及计时器，不加载全局替身。
export default defineConfig({
  plugins: [vue()],
  test: {
    environment: 'jsdom',
    include: [
      'tests/unit/interaction-lifecycle.spec.ts',
      'tests/unit/processing-options.spec.ts',
      'tests/unit/phase-ownership.spec.ts',
      'tests/unit/job-core.spec.ts',
      'tests/components/message-timeline.spec.ts',
      'tests/components/processing-options.spec.ts',
      'tests/components/composer.spec.ts',
    ],
  },
})
