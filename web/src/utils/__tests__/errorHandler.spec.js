import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { categorizeChatError } from '../errorHandler.js'

// 模拟 base.js 在 HTTP 错误时的封装：error.message = readable，error.response.data.detail = 原始 detail
const makeHttpError = ({ status, detail, message }) => {
  // 同步 base.js 的赋值语义：当 detail 是字符串，error.message = detail；当 detail 是对象，error.message = detail.message
  const finalMessage =
    message ||
    (typeof detail === 'string'
      ? detail
      : detail && typeof detail === 'object'
        ? detail.message || detail.error || '请求失败'
        : '请求失败')
  const err = new Error(finalMessage)
  err.response = { status, data: { detail } }
  return err
}

describe('categorizeChatError', () => {
  it('将 429 + token_quota_exceeded 归类为 quota_exceeded 并保留结构化字段', () => {
    const err = makeHttpError({
      status: 429,
      detail: {
        code: 'token_quota_exceeded',
        message: '用户 wangsz 本周 token 额度已用尽',
        quota: 10000000,
        used: 10004522,
        remaining: 0,
        reset_at: '2026-08-03 00:00'
      }
    })
    const result = categorizeChatError(err)
    assert.equal(result.kind, 'quota_exceeded')
    assert.equal(result.httpStatus, 429)
    assert.equal(result.body.message, '用户 wangsz 本周 token 额度已用尽')
    assert.equal(result.body.quota, 10000000)
    assert.equal(result.body.used, 10004522)
    assert.equal(result.body.remaining, 0)
    assert.equal(result.body.resetAt, '2026-08-03 00:00')
  })

  it('将 403 归类为 permission_denied', () => {
    const err = makeHttpError({ status: 403, detail: '权限不足' })
    const result = categorizeChatError(err)
    assert.equal(result.kind, 'permission_denied')
    assert.equal(result.httpStatus, 403)
    assert.equal(result.body.message, '权限不足')
  })

  it('把 apikey.invoke 相关的 403 错误附上 hint 字段', () => {
    const err = makeHttpError({
      status: 403,
      detail: "API Key 鉴权失败：用户未获得 'apikey.invoke' 权限"
    })
    const result = categorizeChatError(err)
    assert.equal(result.kind, 'permission_denied')
    assert.ok(result.body.hint?.includes('apikey.invoke'))
  })

  it('将 500 归类为 generic_error', () => {
    const err = makeHttpError({ status: 500, detail: '服务器错误' })
    const result = categorizeChatError(err)
    assert.equal(result.kind, 'generic_error')
    assert.equal(result.httpStatus, 500)
  })

  it('SSE error event 带 error_type=token_quota_exceeded 也能识别', () => {
    const sseEvent = {
      status: 429,
      payload: {
        chunk: { error_type: 'token_quota_exceeded', error_message: '额度已尽' }
      }
    }
    const result = categorizeChatError(sseEvent)
    assert.equal(result.kind, 'quota_exceeded')
    assert.equal(result.body.message, '额度已尽')
  })

  it('无 HTTP 状态的智能体运行错误使用后端安全文案内联展示', () => {
    const sseEvent = {
      payload: {
        chunk: {
          error_type: 'agent_execution_error',
          error_message: '智能体运行失败，请稍后重试'
        }
      }
    }
    const result = categorizeChatError(sseEvent)
    assert.equal(result.kind, 'generic_error')
    assert.equal(result.title, '智能体运行失败')
    assert.equal(result.body.message, '智能体运行失败，请稍后重试')
  })

  it('没有 HTTP 上下文的裸 Error 返回 null（不强制内联）', () => {
    assert.equal(categorizeChatError(new Error('前端逻辑错误')), null)
  })

  it('null / undefined / 非对象返回 null', () => {
    assert.equal(categorizeChatError(null), null)
    assert.equal(categorizeChatError(undefined), null)
    assert.equal(categorizeChatError('plain string'), null)
  })
})
