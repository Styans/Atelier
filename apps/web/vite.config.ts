import { fileURLToPath, URL } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The dev server proxies the API started by `uv run atelier ui --no-browser`.
export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8710" } },
  build: { outDir: "dist", chunkSizeWarningLimit: 1500 },
  test: { environment: "node" },
});
