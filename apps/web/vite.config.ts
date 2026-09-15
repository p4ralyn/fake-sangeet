import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server is proxied same-origin to the FastAPI backend so the
// HttpOnly; SameSite=Lax session cookie works without any CORS config.
// See docs/design/frontend-api-contract.md "Transport rules".
//
// Target is overridable via API_PROXY_TARGET so the same config works both
// for `npm run dev` on the host (api reachable at localhost:8000) and inside
// docker compose (api reachable at the service name `api:8000`).
const apiProxyTarget = process.env.API_PROXY_TARGET ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      "/api": {
        target: apiProxyTarget,
        changeOrigin: true,
      },
      "/auth": {
        target: apiProxyTarget,
        changeOrigin: true,
      },
    },
  },
});
