import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url))
    }
  },
  server: {
    // 同时监听 localhost 与 127.0.0.1：Windows 上默认 host 只绑 localhost(::1)，
    // 会导致 http://127.0.0.1:5173 打不开（后端 Origin 白名单默认写 127.0.0.1）。
    host: '0.0.0.0',
    port: 5173,
    // 端口被占直接报错，避免静默换端口导致 Origin 白名单失效
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
      },
    },
  },
})
