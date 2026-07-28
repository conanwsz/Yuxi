import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { useAgentThreadState } from '../useAgentThreadState.js'

describe('useAgentThreadState', () => {
  it('刷新后恢复未成功发送的用户消息', () => {
    const originalStorage = globalThis.sessionStorage
    const values = new Map()
    globalThis.sessionStorage = {
      getItem: (key) => values.get(key) || null,
      setItem: (key, value) => values.set(key, value)
    }

    const firstState = { threadStates: {} }
    const first = useAgentThreadState({ chatState: firstState })
    const failedMessage = { id: 'request-1', type: 'human', content: 'first message' }
    first.persistFailedHumanMessages('thread-1', [failedMessage])

    const restoredState = { threadStates: {} }
    const restored = useAgentThreadState({ chatState: restoredState })
    assert.deepEqual(restored.getThreadState('thread-1').failedHumanMessages, [failedMessage])

    if (originalStorage === undefined) delete globalThis.sessionStorage
    else globalThis.sessionStorage = originalStorage
  })

  it('成功创建新请求后清除之前保留的失败消息', () => {
    const originalStorage = globalThis.sessionStorage
    const values = new Map()
    globalThis.sessionStorage = {
      getItem: (key) => values.get(key) || null,
      setItem: (key, value) => values.set(key, value)
    }

    const chatState = { threadStates: {} }
    const state = useAgentThreadState({ chatState })
    state.getThreadState('thread-1').failedHumanMessages.push({
      id: 'req-1785211530000-failed',
      type: 'human',
      content: 'same question'
    })

    state.clearFailedHumanMessages('thread-1')

    assert.deepEqual(state.getThreadState('thread-1').failedHumanMessages, [])
    assert.deepEqual(JSON.parse(values.get('yuxi.failed-human-messages'))['thread-1'], [])

    if (originalStorage === undefined) delete globalThis.sessionStorage
    else globalThis.sessionStorage = originalStorage
  })

  it('历史中已有稍后成功发送的同一问题时清理旧失败副本', () => {
    const originalStorage = globalThis.sessionStorage
    const values = new Map()
    globalThis.sessionStorage = {
      getItem: (key) => values.get(key) || null,
      setItem: (key, value) => values.set(key, value)
    }

    const chatState = { threadStates: {} }
    const state = useAgentThreadState({ chatState })
    state.getThreadState('thread-1').failedHumanMessages.push(
      {
        id: 'req-1785211530000-failed',
        type: 'human',
        content: 'same question'
      },
      {
        id: 'req-1785211535000-other',
        type: 'human',
        content: 'still failed'
      }
    )

    state.reconcileFailedHumanMessages('thread-1', [
      {
        request_id: 'req-1785211540000-success',
        type: 'human',
        content: 'same question',
        delivery_status: 'complete'
      }
    ])

    assert.deepEqual(state.getThreadState('thread-1').failedHumanMessages, [
      {
        id: 'req-1785211535000-other',
        type: 'human',
        content: 'still failed'
      }
    ])

    if (originalStorage === undefined) delete globalThis.sessionStorage
    else globalThis.sessionStorage = originalStorage
  })
})
