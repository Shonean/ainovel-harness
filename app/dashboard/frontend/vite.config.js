import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath } from 'url'
import { rmSync, existsSync } from 'fs'
import { resolve, dirname } from 'path'

const __dirname = dirname(fileURLToPath(import.meta.url))

/**
 * 在 build 开始前强制清空 outDir。
 * 为什么不用 vite 自带的 emptyOutDir？
 *   实测中 emptyOutDir: true 在某些场景（文件被占用 / 权限 / 路径解析）
 *   下不会真正清空，导致 dist 里越积越多不同 hash 的 chunk。
 *   这里用一个简单插件在 buildStart 阶段显式 rm -rf 目录，确保绝对干净。
 */
function cleanDistPlugin() {
  return {
    name: 'clean-dist',
    buildStart(options) {
      const outDir = options.outDir || 'dist'
      const target = resolve(__dirname, outDir)
      if (existsSync(target)) {
        try {
          rmSync(target, { recursive: true, force: true })
          console.log(`[clean-dist] 已清空 ${target}`)
        } catch (e) {
          console.warn(`[clean-dist] 清空失败（不影响构建）: ${e.message}`)
        }
      }
    },
  }
}

export default defineConfig({
  base: './',
  plugins: [react(), cleanDistPlugin()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8765',
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    rollupOptions: {
      output: {
        manualChunks: {
          'react-vendor': ['react', 'react-dom', 'react-router-dom'],
          'echarts-vendor': ['echarts', 'echarts-for-react'],
        },
      },
    },
  },
})
