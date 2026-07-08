import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Single-origin dev: the browser talks to :5173 only; Vite forwards /api (REST and
// the /api/ws/* websockets) to the FastAPI backend on :8050.
export default defineConfig({
  plugins: [react()],
  server: {
    // Bind all interfaces so the dev server answers on BOTH 127.0.0.1 (IPv4) and
    // [::1] (IPv6). Without this, Vite binds only whatever `localhost` resolves to
    // (here ::1), and browsers that try IPv4 first get "connection refused".
    host: true,
    proxy: {
      "/api": {
        target: "http://localhost:8050",
        changeOrigin: true,
        ws: true,
      },
    },
  },
});
