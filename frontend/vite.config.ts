import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const backendPort = env.BACKEND_PORT ?? '8000'
  const backendHost = env.BACKEND_HOST ?? 'localhost'
  const backendUrl = `http://${backendHost}:${backendPort}`

  return {
    plugins: [react()],
    build: {
      outDir: '../src/static',
      assetsDir: 'assets',
    },
    server: {
      port: 3000,
      strictPort: true,
      proxy: {
        '/api': {
          target: backendUrl,
          changeOrigin: true,
        },
        '/health': {
          target: backendUrl,
          changeOrigin: true,
        },
        '/ws': {
          target: `ws://${backendHost}:${backendPort}`,
          ws: true,
        },
      },
    },
    test: {
      environment: 'jsdom',
      globals: true,
    },
  }
})
