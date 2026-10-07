import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'node:path'
import { fontLicenseFiles } from '../packages/design-system/vite/font-license-plugin.js'

function filePathFromUrl(url: URL): string {
  const pathname = decodeURIComponent(url.pathname)
  return pathname.startsWith('/') && pathname[2] === ':' ? pathname.slice(1) : pathname
}

const frontendRoot = filePathFromUrl(new URL('.', import.meta.url))
const designSystemRoot = filePathFromUrl(new URL('../packages/design-system/src', import.meta.url))

export default defineConfig({
  base: '/site-assets/',
  resolve: {
    alias: {
      '@causalagent/design-system': resolve(designSystemRoot),
    },
  },
  plugins: [vue(), fontLicenseFiles()],
  server: {
    fs: { allow: [frontendRoot, designSystemRoot] },
    port: 5175,
    strictPort: true,
    proxy: {
      '/api': {
        target: process.env.VITE_FLASK_ORIGIN || 'http://127.0.0.1:5001',
        changeOrigin: true,
      },
    },
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('node_modules/vue')) return 'vue'
          return undefined
        },
      },
    },
  },
})

