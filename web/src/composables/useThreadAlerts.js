import { computed, reactive } from 'vue'

import { categorizeChatError } from '../utils/errorHandler.js'

/**
 * 会话级别的内联告警池（持久化在浏览器会话存储，不入 LLM 上下文）。
 *
 * 用法：在 AgentChatComponent 等长生命周期组件 mount 时调用一次 `useThreadAlerts()`，
 * 共享一个 reactive threadAlerts 状态；模板里通过 `getThreadAlerts(threadId)`
 * 获取某会话的告警列表（reactive），错误处理路径里 `pushThreadAlert(threadId, category)`。
 *
 * 关键不变量：
 * 1. 切 thread 不会清空旧告警——告警是"那一刻的状态"，换 thread 之后仍然可以看到。
 *    切回原 thread 时会重新出现。
 * 2. 刷新页面后会从浏览器会话存储恢复，关闭或重试后才移除。
 * 3. 告警永远不会被任何 backend API 带上，也不会进入 `threadMessages` / `agentApi.*` 调用。
 */
let _sharedAlerts = null
const THREAD_ALERTS_STORAGE_KEY = 'yuxi.thread-alerts'

const loadThreadAlerts = () => {
  if (typeof sessionStorage === 'undefined') return {}
  try {
    const stored = JSON.parse(sessionStorage.getItem(THREAD_ALERTS_STORAGE_KEY) || '{}')
    return stored && typeof stored === 'object' && !Array.isArray(stored) ? stored : {}
  } catch {
    return {}
  }
}

const persistThreadAlerts = (alerts) => {
  if (typeof sessionStorage === 'undefined') return
  try {
    sessionStorage.setItem(THREAD_ALERTS_STORAGE_KEY, JSON.stringify(alerts))
  } catch {
    // 本地存储不可用时仍保留当前页面内的告警。
  }
}

const _generateId = () => {
  // 不依赖 crypto.randomUUID（部分测试环境缺失），简单够用
  return `alert-${Date.now()}-${Math.floor(Math.random() * 1e6)}`
}

const _ensureShared = () => {
  if (!_sharedAlerts) {
    _sharedAlerts = reactive(loadThreadAlerts())
  }
  return _sharedAlerts
}

export function useThreadAlerts() {
  const threadAlerts = _ensureShared()

  /**
   * 往指定 thread 推一条告警。category 必须来自 `categorizeChatError` 的返回结构。
   * 重复同 kind 的告警会被合并，避免同一错误反复出现多条提示。
   *
   * @param {string} threadId
   * @param {{ kind: string, httpStatus?: number, title: string, body: object }|null|undefined} category
   * @param {{ retry?: { text: string, imageContent: any, attachments: any[], requestId: string } }} [options]
   *        retry：把"待重发的请求载荷"挂到 alert 上，让 ChatAlertBanner 可以渲染"重试"按钮。
   *        一般在 handleSendMessage 失败时挂上；SSE 失败路径不挂（请求已发，不能盲重发）。
   * @returns {string|null} 新告警 id；category 为空时返回 null
   */
  const pushThreadAlert = (threadId, category, options = {}) => {
    if (!threadId || !category) return null
    if (!threadAlerts[threadId]) {
      threadAlerts[threadId] = []
    }
    const list = threadAlerts[threadId]
    const existingSame = list.find((alert) => alert.kind === category.kind)
    if (existingSame) {
      // 同一错误再次发生时，保留一个提示框，并更新其中的文案和重试载荷。
      existingSame.title = category.title
      existingSame.body = {
        ...(category.body || {}),
        ...(options.retry ? { retry: options.retry } : {})
      }
      existingSame.httpStatus = category.httpStatus
      persistThreadAlerts(threadAlerts)
      return existingSame.id
    }

    const now = Date.now()
    const alert = {
      id: _generateId(),
      kind: category.kind,
      title: category.title,
      body: { ...(category.body || {}), ...(options.retry ? { retry: options.retry } : {}) },
      httpStatus: category.httpStatus,
      createdAt: now
    }
    list.push(alert)
    persistThreadAlerts(threadAlerts)
    return alert.id
  }

  /**
   * 关闭某条告警。返回剩余告警数量，便于调试。
   */
  const dismissThreadAlert = (threadId, alertId) => {
    if (!threadId || !alertId) return 0
    const list = threadAlerts[threadId]
    if (!list) return 0
    const idx = list.findIndex((a) => a.id === alertId)
    if (idx === -1) return 0
    list.splice(idx, 1)
    if (list.length === 0) {
      delete threadAlerts[threadId]
    }
    persistThreadAlerts(threadAlerts)
    return list.length
  }

  /**
   * 清空某 thread 的所有告警（一般用于 thread 被删除时）。
   */
  const clearThreadAlerts = (threadId) => {
    if (!threadId) return
    delete threadAlerts[threadId]
    persistThreadAlerts(threadAlerts)
  }

  /**
   * 清理所有已不存在于 validThreadIds 的告警，避免 thread 被删除后 alert 泄漏。
   * 可在 store 维护的 thread 列表变更时调用。
   */
  const pruneOrphanedAlerts = (validThreadIds) => {
    const validSet = new Set(validThreadIds || [])
    for (const key of Object.keys(threadAlerts)) {
      if (!validSet.has(key)) {
        delete threadAlerts[key]
      }
    }
    persistThreadAlerts(threadAlerts)
  }

  /**
   * 取某 thread 的告警列表（reactive，模板里直接用）。
   */
  const getThreadAlerts = (threadId) => {
    if (!threadId) return []
    return threadAlerts[threadId] || []
  }

  /**
   * 给定一个 Error / SSE 事件，自动 categorize 并 push。如果不可分类则静默返回 null。
   * options.retry 会原样透传给 pushThreadAlert，用于在告警上挂"重试载荷"。
   */
  const pushAlertFromError = (threadId, error, options) => {
    const category = categorizeChatError(error)
    return pushThreadAlert(threadId, category, options)
  }

  const totalCount = computed(() =>
    Object.values(threadAlerts).reduce((sum, list) => sum + (list?.length || 0), 0)
  )

  return {
    threadAlerts,
    pushThreadAlert,
    dismissThreadAlert,
    clearThreadAlerts,
    pruneOrphanedAlerts,
    getThreadAlerts,
    pushAlertFromError,
    totalCount
  }
}
