import assert from 'node:assert/strict'
import test from 'node:test'

import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const listPath = join(here, '../../src/components/extensions/SkillCardList.vue')
const source = readFileSync(listPath, 'utf8')

test('推荐位治理弹窗 updated 后同时刷新技能列表与推荐套件', () => {
  // 不能只 fetchRecommendedSuites：下架/删除的是技能（skills 表），必须同时刷新 fetchSkills
  assert.match(source, /@updated="handleRecommendedManageUpdated"/)
  const handler = source.match(/const handleRecommendedManageUpdated = [\s\S]{0,300}?\n\}/)
  assert.ok(handler, '缺少 handleRecommendedManageUpdated')
  assert.match(handler[0], /fetchSkills\(/)
  assert.match(handler[0], /fetchRecommendedSuites\(/)
})

test('已下架（enabled=false）的推荐工作区技能不出现在「推荐」分组', () => {
  // 下架 = 对所有用户（含创建者）从推荐区隐藏；治理入口只保留在「管理推荐位」弹窗
  const block = source.match(/const userPublishedSuites = computed\(\(\) => \{[\s\S]*?\n\}\)/)
  assert.ok(block, '缺少 userPublishedSuites')
  assert.match(block[0], /skill\.is_recommended_workspace === true\s*&&\s*skill\.enabled !== false/)
})

