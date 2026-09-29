import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
import { fontLicenseFiles } from '../packages/design-system/vite/font-license-plugin.js'
import type { Alias } from 'vite'

function filePathFromUrl(url: URL): string {
  const pathname = decodeURIComponent(url.pathname)
  return pathname.startsWith('/') && pathname[2] === ':' ? pathname.slice(1) : pathname
}

const frontendRoot = filePathFromUrl(new URL('.', import.meta.url))
const designSystemRoot = filePathFromUrl(new URL('../packages/design-system/src', import.meta.url))
const designSystemAliases: Alias[] = [
  {
    find: /^@causalagent\/design-system\/styles\.css$/,
    replacement: filePathFromUrl(new URL('../packages/design-system/src/styles/index.css', import.meta.url)),
  },
  {
    find: /^@causalagent\/design-system$/,
    replacement: filePathFromUrl(new URL('../packages/design-system/src/index.ts', import.meta.url)),
  },
]

export default defineConfig({
  base: '/admin/',
  plugins: [vue(), fontLicenseFiles()],
  resolve: {
    alias: designSystemAliases,
  },
  server: {
    fs: { allow: [frontendRoot, designSystemRoot] },
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': {
        target: process.env.VITE_FLASK_ORIGIN || 'http://127.0.0.1:5001',
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./tests/setup.ts'],
    include: ['./tests/*.spec.ts'],
  },
})
