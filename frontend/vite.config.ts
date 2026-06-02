import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const apiProxyTarget = process.env.VITE_API_PROXY_TARGET ?? "http://127.0.0.1:8502";
const identityProxyTarget = process.env.VITE_IDENTITY_PROXY_TARGET ?? "http://127.0.0.1:8500";
const adminApiKey = process.env.PAYMENT_ADMIN_API_KEY ?? "";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 7502,
    host: "0.0.0.0",
    proxy: {
      "/api": {
        target: apiProxyTarget,
        changeOrigin: true,
        configure: (proxy) => {
          proxy.on("proxyReq", (proxyReq) => {
            if (adminApiKey) {
              proxyReq.setHeader("X-Admin-Key", adminApiKey);
            }
          });
        },
      },
      "/health": {
        target: apiProxyTarget,
        changeOrigin: true,
      },
      "/docs": {
        target: apiProxyTarget,
        changeOrigin: true,
      },
      "/openapi.json": {
        target: apiProxyTarget,
        changeOrigin: true,
      },
      "/identity-api": {
        target: identityProxyTarget,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/identity-api/, "/api/v1"),
      },
    },
  },
});
