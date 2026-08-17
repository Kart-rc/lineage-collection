import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Port 8000 is a common default and is often already taken on a developer machine.
// Keep the historical value as the default so nothing changes for most people, but
// let both the dev server and its proxy follow LINEAGE_API_PORT together — moving
// one without the other would silently proxy to the wrong backend.
const apiPort = process.env.LINEAGE_API_PORT ?? "8000";
const apiTarget = `http://127.0.0.1:${apiPort}`;
const proxy = { "/api": apiTarget, "/healthz": apiTarget };

export default defineConfig({
  plugins: [react()],
  build: {
    assetsDir: "assets",
    sourcemap: false,
    rollupOptions: {
      output: {
        assetFileNames: "assets/[name]-[hash][extname]",
        chunkFileNames: "assets/[name]-[hash].js",
        entryFileNames: "assets/[name]-[hash].js",
      },
    },
  },
  server: { proxy },
  preview: { proxy },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/test/setup.ts",
    css: true,
  },
});
