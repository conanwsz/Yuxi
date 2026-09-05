import assert from 'node:assert/strict'
import test from 'node:test'

// 通过设置 import.meta 路径让 skill_api 引用到我们 mock 的 base.js
// 实际上更简单：直接 mock global.fetch + Pinia 相关全局
// 这里我们用 dynamic import 走 Vite-style alias 不行，转为只验证 skill_api 中
// 公开的函数名存在 —— 真正的 wire 行为在后端 router 测试里已覆盖。

// 由于 base.js 依赖 Pinia store，Node test 直接 import 会失败。
// 改为：动态 import + 用 Module._cache hack 不优雅。
// 退而求其次：验证函数签名存在（按 ESM 静态扫描）。

import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const skillApiPath = join(here, '../../src/apis/skill_api.js')
const source = readFileSync(skillApiPath, 'utf8')

test('skill_api.js 暴露 8 个推荐套件相关方法', () => {
  const required = [
    'listRecommendedSuites',
    'listRecommendedSuitesAdmin',
    'getRecommendedSuite',
    'createRecommendedSuite',
    'updateRecommendedSuite',
    'setRecommendedSuiteEnabled',
    'deleteRecommendedSuite',
    'prepareSuiteUpload'
  ]
  for (const name of required) {
    assert.match(source, new RegExp(`export const ${name}\\b`), `缺少导出: ${name}`)
  }
})

test('推荐套件公共列表走 /api/system/skills/recommended-suites', () => {
  assert.match(
    source,
    /apiGet\(\s*[`'"]\$\{BASE_URL\}\/recommended-suites[`'"]\s*\)/
  )
})

test('管理列表走 admin 路径', () => {
  assert.match(
    source,
    /apiAdminGet\(\s*[`'"]\$\{BASE_URL\}\/recommended-suites\/admin[`'"]\s*\)/
  )
})

test('启停接口使用 PATCH /enabled', () => {
  assert.match(
    source,
    /apiAdminPatch\(\s*[`'"]\$\{BASE_URL\}\/recommended-suites\/\$\{id\}\/enabled[`'"]/
  )
})

test('套件 zip 上传走 multipart POST /api/skills/import/suite-prepare', () => {
  assert.match(source, /USER_BASE_URL\}\/import\/suite-prepare/)
  assert.match(source, /FormData\(\)/)
})
