import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const backendPort = env.BACKEND_PORT ?? '8000'
  const backendHost = env.BACKEND_HOST ?? '127.0.0.1'
  const backendUrl = `http://${backendHost}:${backendPort}`

  return {
    plugins: [react()],
    build: {
      outDir: '../src/static',
      assetsDir: 'assets',
    },
    server: {
      host: '127.0.0.1', // Force IPv4 (Windows localhost resolves to IPv6 ::1)
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
          target: backendUrl,
          ws: true,
          changeOrigin: true,
          secure: false,
        },
      },
    },
    test: {
      environment: 'jsdom',
      globals: true,
    },
  }
})
