import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Built assets are served by the agent server under /ui (see agent_server/webapp.py).
// In dev, proxy the agent API + the SPA's /api routes to the local agent server on :8000.
export default defineConfig(({ command }) => ({
  base: command === "build" ? "/ui/" : "/",
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:8000",
      "/invocations": "http://localhost:8000",
    },
  },
  build: { outDir: "dist", emptyOutDir: true },
}));
