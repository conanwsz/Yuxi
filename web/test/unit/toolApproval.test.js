import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  DEFAULT_TOOL_APPROVAL_MODE,
  resolveToolApprovalMode
} from '../../src/utils/toolApproval.js'

test('工具审批模式未显式配置时默认完全信任', () => {
  assert.equal(DEFAULT_TOOL_APPROVAL_MODE, 'always_trust')
  assert.equal(
    resolveToolApprovalMode({
      hasThread: true,
      threadMode: null,
      agentMode: null,
      savedMode: null
    }),
    'always_trust'
  )
})

test('工具审批模式保留线程和用户的显式选择', () => {
  assert.equal(
    resolveToolApprovalMode({
      hasThread: true,
      threadMode: 'default',
      agentMode: 'always_trust',
      savedMode: 'always_trust'
    }),
    'default'
  )
  assert.equal(
    resolveToolApprovalMode({
      hasThread: false,
      threadMode: null,
      agentMode: null,
      savedMode: 'default'
    }),
    'default'
  )
})
