import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url))
    }
  },
  test: {
    // G6.1：jsdom 环境（localStorage/DOM 组件挂载）；显式 import（不启用 globals）
    environment: 'jsdom',
    globals: false,
    coverage: {
      provider: 'v8',
      include: ['src/**/*.{ts,vue}'],
      exclude: ['src/**/*.test.ts', 'src/main.ts'],
      // M7：前端覆盖率门禁（CI 跑 npm run coverage 强制校验）。
      // 门槛定在实测值（Stmts 39.3 / Branch 28.6 / Funcs 33.9 / Lines 38.5）之下，
      // 既阻断「新增代码把覆盖率拉低」的回归，又留有正常迭代空间；
      // 补测试应提高实际值，门槛只在覆盖率下降时报警。
      thresholds: {
        statements: 35,
        branches: 25,
        functions: 30,
        lines: 35,
      },
    },
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
