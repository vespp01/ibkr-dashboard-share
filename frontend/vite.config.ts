import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', 'VITE_');
  const apiTarget = env.VITE_API_TARGET ?? 'http://127.0.0.1:8000';

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
