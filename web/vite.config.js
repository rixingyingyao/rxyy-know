import { fileURLToPath, URL } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig(({ mode }) => {
  // eslint-disable-next-line no-undef
  const env = loadEnv(mode, process.cwd(), '')
  return {
    plugins: [vue()],
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url))
      }
    },
    server: {
      proxy: {
        '^/api': {
          target: env.VITE_API_URL || 'http://api:5050',
          changeOrigin: true
        }
      },
      watch: {
        usePolling: true,
        ignored: ['**/node_modules/**', '**/dist/**'],
      },
      host: '0.0.0.0',
      // 反代/隧道域名通过 VITE_ALLOWED_HOSTS 传入（逗号分隔），否则 vite 会拒绝该 Host
      allowedHosts: (env.VITE_ALLOWED_HOSTS || 'localhost')
        .split(',')
        .map((host) => host.trim())
        .filter(Boolean),
    }
  }
})
