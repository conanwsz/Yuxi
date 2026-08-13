import assert from 'node:assert/strict'
import test from 'node:test'

import { buildRemoteSkillRetryRequests } from '../../src/utils/remoteSkillRetry.js'

test('远程 Skill 重试使用上游原始名称而不是本地防重名 slug', () => {
  const requests = buildRemoteSkillRetryRequests([
    {
      source_type: 'remote',
      source: 'anthropics/skills',
      slug: 'pdf-v2',
      original_name: 'pdf'
    },
    {
      source_type: 'remote',
      source: 'anthropics/skills',
      slug: 'pptx',
      original_name: 'pptx'
    }
  ])

  assert.deepEqual(requests, [{ source: 'anthropics/skills', skills: ['pdf', 'pptx'] }])
})

test('缺少原始名称时保留现有 slug 以兼容旧草稿', () => {
  const requests = buildRemoteSkillRetryRequests([
    { source_type: 'remote', source: 'example/skills', slug: 'legacy-skill' }
  ])

  assert.deepEqual(requests, [{ source: 'example/skills', skills: ['legacy-skill'] }])
})
