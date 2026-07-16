import { fileURLToPath } from "node:url";

import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

const projectEnvDir = fileURLToPath(new URL("..", import.meta.url));

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, projectEnvDir, "");
  const apiProxyTarget =
    process.env.VITE_API_PROXY_TARGET ?? env.VITE_API_PROXY_TARGET ?? "http://127.0.0.1:8502";
  const identityProxyTarget =
    process.env.VITE_IDENTITY_PROXY_TARGET ?? env.VITE_IDENTITY_PROXY_TARGET ?? "http://127.0.0.1:8500";

  return {
    envDir: projectEnvDir,
    plugins: [react()],
    resolve: {
      preserveSymlinks: true,
    },
    server: {
      port: 7502,
      host: "0.0.0.0",
      proxy: {
        "/api": {
          target: apiProxyTarget,
          changeOrigin: true,
        },
        "/health": {
          target: apiProxyTarget,
          changeOrigin: true,
        },
        "/ready": {
          target: apiProxyTarget,
          changeOrigin: true,
          rewrite: () => "/api/v1/ready",
        },
        "/ui": {
          target: apiProxyTarget,
          changeOrigin: true,
        },
        "/docs": {
          target: apiProxyTarget,
          changeOrigin: true,
        },
        "/redoc": {
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
  };
});
