import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', 'VITE_');
  // :8001 is the compose backend's host port; :8000 is often another container.
  const apiTarget = env.VITE_API_TARGET ?? 'http://127.0.0.1:8001';

  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        // Proxy FastAPI calls during dev so the SPA can use relative URLs.
        '/api': {
          target: apiTarget,
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/api/, ''),
        },
      },
    },
  };
});
