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

// In production the built app is served by `vite preview` behind Caddy, which
// forwards the real Host header. Vite rejects a Host it does not recognise with
// a bare 403 and no explanation, so the deployed hostname must be declared.
// SITE_HOST is the same variable Caddy uses, so the two cannot drift.
const siteHost = process.env.SITE_HOST;
const proxy = {
  "/api": { target: apiProxyTarget, changeOrigin: true },
  "/auth": { target: apiProxyTarget, changeOrigin: true },
};

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy,
  },
  preview: {
    host: true,
    port: 5173,
    // localhost entries keep `npm run preview` usable locally; SITE_HOST adds
    // whatever the deployment is actually served as.
    allowedHosts: ["localhost", "127.0.0.1", ...(siteHost ? [siteHost] : [])],
    proxy,
  },
});
