import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { useThreadAlerts } from '../useThreadAlerts.js'

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

const quotaCategory = {
  kind: 'quota_exceeded',
  httpStatus: 429,
  title: 'Token 额度已用尽',
  body: { message: '本周 token 额度已用尽', remaining: 0 }
}

const permCategory = {
  kind: 'permission_denied',
  httpStatus: 403,
  title: '权限不足',
  body: { message: '无权调用' }
}

describe('useThreadAlerts', () => {
  it('push 之后能用 getThreadAlerts 拿到', () => {
    const { pushThreadAlert, getThreadAlerts } = useThreadAlerts()
    const id = pushThreadAlert('thread-a', quotaCategory)
    assert.ok(id)
    const list = getThreadAlerts('thread-a')
    assert.equal(list.length, 1)
    assert.equal(list[0].kind, 'quota_exceeded')
    assert.equal(list[0].title, 'Token 额度已用尽')
  })

  it('同 kind 重复 push 始终合并为一条告警', async () => {
    const { pushThreadAlert, getThreadAlerts } = useThreadAlerts()
    const threadId = 'thread-dedupe'
    const id1 = pushThreadAlert(threadId, quotaCategory)
    getThreadAlerts(threadId)[0].createdAt -= 6000
    await sleep(50)
    const id2 = pushThreadAlert(threadId, quotaCategory)
    assert.equal(id1, id2)
    assert.equal(getThreadAlerts(threadId).length, 1)
  })

  it('不同 kind 不互相去重', () => {
    const { pushThreadAlert, getThreadAlerts } = useThreadAlerts()
    const threadId = 'thread-mix'
    pushThreadAlert(threadId, quotaCategory)
    pushThreadAlert(threadId, permCategory)
    assert.equal(getThreadAlerts(threadId).length, 2)
  })

  it('dismiss 删除指定告警，空数组时清理 threadId 键', () => {
    const { pushThreadAlert, dismissThreadAlert, threadAlerts } = useThreadAlerts()
    const threadId = 'thread-dismiss'
    const id = pushThreadAlert(threadId, quotaCategory)
    const remaining = dismissThreadAlert(threadId, id)
    assert.equal(remaining, 0)
    assert.equal(threadAlerts[threadId], undefined)
  })

  it('clearThreadAlerts 直接清空', () => {
    const { pushThreadAlert, clearThreadAlerts, threadAlerts } = useThreadAlerts()
    const threadId = 'thread-clear'
    pushThreadAlert(threadId, quotaCategory)
    pushThreadAlert(threadId, permCategory)
    clearThreadAlerts(threadId)
    assert.equal(threadAlerts[threadId], undefined)
  })

  it('pruneOrphanedAlerts 清掉不在 validThreadIds 列表里的告警', () => {
    const { pushThreadAlert, pruneOrphanedAlerts, threadAlerts } = useThreadAlerts()
    const alive = 'prune-alive'
    const dead1 = 'prune-dead-1'
    const dead2 = 'prune-dead-2'
    pushThreadAlert(alive, quotaCategory)
    pushThreadAlert(dead1, quotaCategory)
    pushThreadAlert(dead2, permCategory)
    pruneOrphanedAlerts([alive])
    assert.ok(threadAlerts[alive])
    assert.equal(threadAlerts[dead1], undefined)
    assert.equal(threadAlerts[dead2], undefined)
  })

  it('切换 thread 不影响已存在的告警', () => {
    const { pushThreadAlert, getThreadAlerts } = useThreadAlerts()
    pushThreadAlert('thread-1', quotaCategory)
    pushThreadAlert('thread-2', permCategory)
    assert.equal(getThreadAlerts('thread-1').length, 1)
    assert.equal(getThreadAlerts('thread-2').length, 1)
  })

  it('category 为空时 push 返回 null 不写入', () => {
    const { pushThreadAlert, getThreadAlerts } = useThreadAlerts()
    assert.equal(pushThreadAlert('thread-null', null), null)
    assert.equal(pushThreadAlert('thread-null', undefined), null)
    assert.equal(getThreadAlerts('thread-null').length, 0)
  })

  it('threadId 为空时 push 返回 null', () => {
    const { pushThreadAlert } = useThreadAlerts()
    assert.equal(pushThreadAlert('', quotaCategory), null)
    assert.equal(pushThreadAlert(null, quotaCategory), null)
  })

  it('totalCount 汇总所有 thread 的告警数', () => {
    // 共享 reactive 单例，断言"在已有基础上 +3"以避开测试间污染
    const { pushThreadAlert, totalCount } = useThreadAlerts()
    const before = totalCount.value
    pushThreadAlert('tc-1', quotaCategory)
    pushThreadAlert('tc-1', permCategory)
    pushThreadAlert('tc-2', quotaCategory)
    assert.equal(totalCount.value, before + 3)
  })

  it('retry payload 持久化到 body.retry', () => {
    const { pushThreadAlert, getThreadAlerts } = useThreadAlerts()
    const retry = { text: 'hello', imageContent: null, attachments: [], requestId: 'r-1' }
    pushThreadAlert('thread-retry-1', quotaCategory, { retry })
    const list = getThreadAlerts('thread-retry-1')
    assert.equal(list.length, 1)
    assert.deepEqual(list[0].body.retry, retry)
  })

  it('不传 retry 时 body.retry 为 undefined', () => {
    const { pushThreadAlert, getThreadAlerts } = useThreadAlerts()
    pushThreadAlert('thread-retry-2', quotaCategory)
    const list = getThreadAlerts('thread-retry-2')
    assert.equal(list[0].body.retry, undefined)
  })

  it('同 kind 重复 push 时，最新 retry 覆盖旧 retry', async () => {
    const { pushThreadAlert, getThreadAlerts } = useThreadAlerts()
    const threadId = 'thread-retry-dedupe'
    const retry1 = { text: 'first', imageContent: null, attachments: [], requestId: 'r-1' }
    const retry2 = { text: 'second', imageContent: null, attachments: [], requestId: 'r-2' }
    pushThreadAlert(threadId, quotaCategory, { retry: retry1 })
    getThreadAlerts(threadId)[0].createdAt -= 6000
    await sleep(20)
    pushThreadAlert(threadId, quotaCategory, { retry: retry2 })
    const list = getThreadAlerts(threadId)
    assert.equal(list.length, 1)
    assert.equal(list[0].body.retry.text, 'second')
    assert.equal(list[0].body.retry.requestId, 'r-2')
  })

  it('pushAlertFromError 转发 options.retry', () => {
    const { pushAlertFromError, getThreadAlerts } = useThreadAlerts()
    const err = Object.assign(new Error('quota'), {
      response: { status: 429, data: { detail: { code: 'token_quota_exceeded', message: 'm' } } }
    })
    const retry = { text: 'retry-text', imageContent: null, attachments: [], requestId: 'r-x' }
    const id = pushAlertFromError('thread-retry-3', err, { retry })
    assert.ok(id)
    const list = getThreadAlerts('thread-retry-3')
    assert.equal(list[0].body.retry.text, 'retry-text')
  })

  it('写入浏览器会话存储，供刷新后恢复', () => {
    const originalStorage = globalThis.sessionStorage
    const values = new Map()
    globalThis.sessionStorage = {
      getItem: (key) => values.get(key) || null,
      setItem: (key, value) => values.set(key, value)
    }

    const { pushThreadAlert } = useThreadAlerts()
    pushThreadAlert('thread-storage', quotaCategory)
    const stored = JSON.parse(values.get('yuxi.thread-alerts'))
    assert.equal(stored['thread-storage'][0].kind, 'quota_exceeded')

    if (originalStorage === undefined) delete globalThis.sessionStorage
    else globalThis.sessionStorage = originalStorage
  })
})
