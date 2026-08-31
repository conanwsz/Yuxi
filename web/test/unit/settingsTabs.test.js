import assert from 'node:assert/strict'
import test from 'node:test'

import { getAvailableSettingsTabs } from '../../src/utils/settingsTabs.js'

const createUser = ({ role = 'user', permissions = [] } = {}) => ({
  isLoggedIn: true,
  isSuperAdmin: role === 'superadmin',
  userRole: role,
  hasPermission: (permission) => role === 'superadmin' || permissions.includes(permission)
})

test('普通用户进入设置页时不开放无权访问的管理页签', () => {
  const user = createUser({ permissions: ['knowledge.read'] })

  assert.deepEqual(getAvailableSettingsTabs(user), ['account', 'agentEnv'])
})

test('设置管理页签分别遵循对应的后端权限边界', () => {
  const userManager = createUser({ permissions: ['users.read'] })
  const departmentManager = createUser({ permissions: ['departments.read'] })
  const admin = createUser({ role: 'admin' })

  assert.deepEqual(getAvailableSettingsTabs(userManager), ['account', 'agentEnv', 'user'])
  assert.deepEqual(getAvailableSettingsTabs(departmentManager), [
    'account',
    'agentEnv',
    'department'
  ])
  assert.deepEqual(getAvailableSettingsTabs(admin), ['account', 'agentEnv', 'ocr'])
})
