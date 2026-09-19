import { fileURLToPath, URL } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

import { minioPublicRewrite } from './src/utils/minioPublicRewrite.js'

// 生产构建剥离 console.log / debug / info / warn / trace 调用:
//   走 inline Vite plugin,在 transform 阶段把 console.X( 改写为 0&&console.X(
//   后续 Oxc minifier 看到 0 && X 短路,会把整段 call expression tree-shake 掉
//
// 为什么不走 build.rolldownOptions.output.minify.compress.drop:
//   Vite 8.1.3 + Rolldown 1.1.4 的 Oxc minifier 当前不消费 drop / dropConsole 字段
//   (2026-09 经多次 build 验证无效),改用纯 transform 改写
//
// 不处理 string literal / template literal 内的 console.X(:
//   当前 transform 是 regex 字符串替换,简单且对 Yuxi 业务代码安全
//   .vue 模板里的 @click="console.log(msg)" 也会被改写为
//   @click="0&&console.log(msg)" (短路,Vue 编译后等价于 0,handler 不调用 console.log)
//
// 保留 console.error: 后续接入 Sentry 时,在 utils/logger.js 统一上报后再替换
//
// 验证: pnpm build && grep -rohE "console\.(log|debug|info|warn|trace)\s*\(" dist/ | wc -l
// 期待输出: 0 (Yuxi 业务代码) + 第三方库的剩余 (KaTeX / markdown-it / 等)
const stripDebugConsolePlugin = {
  name: 'yuxi:strip-debug-console',
  apply: 'build',
  transform(code, id) {
    if (id.includes('node_modules')) return
    if (!/\.(js|ts|vue|jsx|tsx|mjs|cjs)$/.test(id)) return
    return {
      code: code.replace(
        /console\.(log|debug|info|warn|trace)\s*\(/g,
        '0&&console.$1('
      ),
      map: null
    }
  }
}

export default defineConfig(({ mode }) => {
  // eslint-disable-next-line no-undef
  const env = loadEnv(mode, process.cwd(), '')
  return {
    plugins: [vue(), stripDebugConsolePlugin],
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url))
      }
    },
    // 显式锁死 sourcemap = false,防止默认值变化或外部 flag 覆盖
    build: {
      sourcemap: false
    },
    server: {
      allowedHosts: ['superchuchu.t.cn-np.com', /\.cn-np\.com$/],
      proxy: {
        '^/api': {
          target: env.VITE_API_URL || 'http://api:5050',
          changeOrigin: true
        },
        '^/minio/public/': {
          target: env.VITE_MINIO_URL || 'http://minio:9000',
          changeOrigin: true,
          // vuln-0007: 反代 rewrite 必须在归一化后的路径上重新断言 /public/ 前缀,
          // 避免"原始路径判定、归一化路径执行"的语义错位导致 .. 点段逃逸。
          rewrite: minioPublicRewrite
        }
      },
      watch: {
        usePolling: true,
        ignored: ['**/node_modules/**', '**/dist/**']
      },
      host: '0.0.0.0'
    }
  }
})
