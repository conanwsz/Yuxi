import assert from 'node:assert/strict'
import test from 'node:test'

import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const modalPath = join(here, '../../src/components/extensions/RecommendedSuitesManageModal.vue')
const source = readFileSync(modalPath, 'utf8')

test('弹窗提供「推荐工作区技能」tab', () => {
  assert.match(source, /tab="推荐工作区技能"/)
  assert.match(source, /key="workspace-skills"/)
})

test('推荐工作区技能表格含状态与操作列', () => {
  assert.match(source, /workspaceSkillColumns/)
  // 状态标签：使用中 / 已下架
  assert.match(source, /使用中/)
  assert.match(source, /已下架/)
})

test('操作按钮：下架 / 重新上架 / 删除（含不可恢复文案）', () => {
  assert.match(source, /下架/)
  assert.match(source, /重新上架/)
  assert.match(source, /删除后不可恢复/)
})

test('治理操作调用 skillApi 三个治理方法并刷新列表与父级', () => {
  assert.match(source, /skillApi\.listRecommendedWorkspaceSkillsAdmin\(/)
  assert.match(source, /skillApi\.setRecommendedWorkspaceSkillEnabled\(/)
  assert.match(source, /skillApi\.deleteRecommendedWorkspaceSkill\(/)
  // 弹窗打开时同时拉取套件与推荐工作区技能
  assert.match(source, /fetchAdminList\(\)[\s\S]{0,120}fetchWorkspaceSkills\(\)/)
})

test('治理成功后 emit updated 通知父组件刷新推荐栏', () => {
  const toggle = source.match(/handleToggleSkillEnabled[\s\S]{0,600}?\n\}/)
  assert.ok(toggle, '缺少 handleToggleSkillEnabled')
  assert.match(toggle[0], /emit\('updated'\)/)
  const remove = source.match(/handleDeleteWorkspaceSkill[\s\S]{0,600}?\n\}/)
  assert.ok(remove, '缺少 handleDeleteWorkspaceSkill')
  assert.match(remove[0], /emit\('updated'\)/)
})
