import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

// 仅运行真实纯函数及 Vue 服务端组件渲染，不加载全局替身或模拟图形库。
export default defineConfig({
  plugins: [vue()],
  test: {
    environment: 'node',
    maxWorkers: 1,
    include: [
      'tests/unit/cdfm-graph.spec.ts',
      'tests/unit/report-document.spec.ts',
      'tests/components/cdfm-graph.spec.ts',
    ],
  },
})
