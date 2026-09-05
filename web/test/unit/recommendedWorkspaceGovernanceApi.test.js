import assert from 'node:assert/strict'
import test from 'node:test'

import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const skillApiPath = join(here, '../../src/apis/skill_api.js')
const source = readFileSync(skillApiPath, 'utf8')

test('skill_api.js 暴露 3 个推荐位治理方法', () => {
  const required = [
    'listRecommendedWorkspaceSkillsAdmin',
    'setRecommendedWorkspaceSkillEnabled',
    'deleteRecommendedWorkspaceSkill'
  ]
  for (const name of required) {
    assert.match(source, new RegExp(`export const ${name}\\b`), `缺少导出: ${name}`)
  }
})

test('推荐位治理方法挂进 skillApi 聚合对象', () => {
  const aggregate = source.slice(source.indexOf('export const skillApi'))
  for (const name of [
    'listRecommendedWorkspaceSkillsAdmin',
    'setRecommendedWorkspaceSkillEnabled',
    'deleteRecommendedWorkspaceSkill'
  ]) {
    assert.match(aggregate, new RegExp(`\\b${name}\\b`), `skillApi 聚合对象缺少: ${name}`)
  }
})

test('管理列表走 apiAdminGet ${BASE_URL}/recommended-workspace/admin', () => {
  assert.match(
    source,
    /apiAdminGet\(\s*[`'"]\$\{BASE_URL\}\/recommended-workspace\/admin[`'"]\s*\)/
  )
})

test('上下架走 apiAdminPatch .../recommended-workspace/${slug}/enabled，body 含 enabled', () => {
  assert.match(
    source,
    /apiAdminPatch\(\s*[`'"]\$\{BASE_URL\}\/recommended-workspace\/\$\{encodeURIComponent\(slug\)\}\/enabled[`'"]/
  )
  assert.match(
    source,
    /setRecommendedWorkspaceSkillEnabled[\s\S]{0,200}\{ enabled \}/
  )
})

test('删除走 apiAdminDelete .../recommended-workspace/${slug}', () => {
  assert.match(
    source,
    /apiAdminDelete\(\s*[`'"]\$\{BASE_URL\}\/recommended-workspace\/\$\{encodeURIComponent\(slug\)\}[`'"]\s*\)/
  )
})
