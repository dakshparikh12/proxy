// Vite static build for the connect page (Doc 08 §2.7). A tiny static app — the
// GitHub-App install flow + the REST readiness poll + invite instructions. In prod the
// /connect/status and /connect/install/start calls hit the control_plane app on the same
// origin; the dev server proxies them there so the poll works locally.
import { defineConfig } from "vite";

export default defineConfig({
  root: ".",
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
  server: {
    proxy: {
      "/connect": {
        target: process.env.PROXY_CONTROL_PLANE_URL || "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
