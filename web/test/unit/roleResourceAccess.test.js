import assert from 'node:assert/strict'
import test from 'node:test'

import { cloneRolePermissionConfig } from '../../src/utils/roleResourceAccess.js'

test('复制角色权限同时复制功能权限和数据权限并断开引用', () => {
  const source = {
    permissions: ['users.read', 'agents.read'],
    resource_access: {
      models: {
        mode: 'selected',
        allowed: ['provider:chat-model'],
        defaults: { chat: 'provider:chat-model' }
      },
      tools: { mode: 'selected', allowed: ['web_search'] },
      mcp_servers: { mode: 'none', allowed: [] }
    }
  }

  const copy = cloneRolePermissionConfig(source)
  copy.permissions.push('agents.update')
  copy.resource_access.models.allowed.push('provider:second-model')
  copy.resource_access.models.defaults.chat = 'provider:second-model'

  assert.deepEqual(source.permissions, ['users.read', 'agents.read'])
  assert.deepEqual(source.resource_access.models.allowed, ['provider:chat-model'])
  assert.equal(source.resource_access.models.defaults.chat, 'provider:chat-model')
  assert.deepEqual(copy.permissions, ['users.read', 'agents.read', 'agents.update'])
  assert.deepEqual(copy.resource_access.tools.allowed, ['web_search'])
})
