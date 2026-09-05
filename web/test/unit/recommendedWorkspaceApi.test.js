import assert from 'node:assert/strict'
import test from 'node:test'

import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const skillApiPath = join(here, '../../src/apis/skill_api.js')
const source = readFileSync(skillApiPath, 'utf8')

test('skill_api.js 暴露 3 个推荐工作区方法', () => {
  const required = [
    'listRecommendedWorkspace',
    'confirmRecommendedWorkspaceInstall',
    'installRecommendedWorkspaceToPersonal'
  ]
  for (const name of required) {
    assert.match(source, new RegExp(`export const ${name}\\b`), `缺少导出: ${name}`)
  }
})

test('listRecommendedWorkspace 走 /api/skills/recommended-workspace', () => {
  assert.match(
    source,
    /apiGet\(\s*[`'"]\$\{USER_BASE_URL\}\/recommended-workspace[`'"]\s*\)/
  )
})

test('confirmRecommendedWorkspaceInstall 走 POST /import/install-to-recommended-workspace，body 含 draft_id 与 slugs', () => {
  assert.match(
    source,
    /apiPost\(\s*[`'"]\$\{USER_BASE_URL\}\/import\/install-to-recommended-workspace[`'"]/
  )
  assert.match(source, /draft_id:\s*draftId/)
  assert.match(source, /slugs/)
})

test('installRecommendedWorkspaceToPersonal 走 POST .../<slug>/install-to-personal', () => {
  assert.match(
    source,
    /apiPost\(\s*[`'"]\$\{USER_BASE_URL\}\/recommended-workspace\/\$\{encodeURIComponent\(slug\)\}\/install-to-personal[`'"]/
  )
})
