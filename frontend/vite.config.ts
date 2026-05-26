import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const apiProxyTarget = process.env.VITE_API_PROXY_TARGET ?? "http://127.0.0.1:8010";
const adminApiKey = process.env.PAYMENT_ADMIN_API_KEY ?? "";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 6750,
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
    },
  },
});
