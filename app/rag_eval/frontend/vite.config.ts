import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import { fontLicenseFiles } from "../../../packages/design-system/vite/font-license-plugin.js";

function filePathFromUrl(url: URL): string {
  const pathname = decodeURIComponent(url.pathname);
  return pathname.startsWith("/") && pathname[2] === ":" ? pathname.slice(1) : pathname;
}

const frontendRoot = filePathFromUrl(new URL(".", import.meta.url));
const designSystemRoot = filePathFromUrl(new URL("../../../packages/design-system/src", import.meta.url));
const designSystemStyles = filePathFromUrl(new URL("../../../packages/design-system/src/styles/index.css", import.meta.url));

export default defineConfig({
  plugins: [vue(), fontLicenseFiles()],
  resolve: {
    alias: [
      { find: "@causalagent/design-system/styles.css", replacement: designSystemStyles },
      { find: "@causalagent/design-system", replacement: designSystemRoot },
    ],
  },
  base: "/rag-eval/",
  server: {
    fs: { allow: [frontendRoot, designSystemRoot] },
    port: 5176,
    strictPort: true,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:5001",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "../frontend_dist",
    emptyOutDir: true,
  },
});
