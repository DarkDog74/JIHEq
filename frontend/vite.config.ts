import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 部署在反向代理的子路径下时改这里（首尾都要有斜杠）。
// 也可打包时用环境变量覆盖：VITE_BASE_PATH=/foo/bar/ npm run build
const BASE_PATH = process.env.VITE_BASE_PATH || '/chuilei/jihe/'

export default defineConfig({
  // base 决定 index.html 里 /assets/... 的引用前缀。不设置的话浏览器会去
  // 域名根目录找资源，代理匹配不到，页面白屏。
  base: BASE_PATH,
  plugins: [vue()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8765',
    },
  },
})
