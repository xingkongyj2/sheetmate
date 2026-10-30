import { fileURLToPath, URL } from 'node:url'

import tailwindcss from '@tailwindcss/vite'
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

// 开发时把 /api 转发到后端（默认 127.0.0.1:8000），这样 Cookie 登录和 SSE 都是同源
export default defineConfig({
  plugins: [vue(), tailwindcss()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: process.env.SHEETMATE_API ?? 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
})
