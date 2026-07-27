import { computed, reactive } from 'vue'

import { categorizeChatError } from '../utils/errorHandler.js'

/**
 * 会话级别的内联告警池（仅前端内存，不持久化、不入 LLM 上下文）。
 *
 * 用法：在 AgentChatComponent 等长生命周期组件 mount 时调用一次 `useThreadAlerts()`，
 * 共享一个 reactive threadAlerts 状态；模板里通过 `getThreadAlerts(threadId)`
 * 获取某会话的告警列表（reactive），错误处理路径里 `pushThreadAlert(threadId, category)`。
 *
 * 关键不变量：
 * 1. 切 thread 不会清空旧告警——告警是"那一刻的状态"，换 thread 之后仍然可以看到。
 *    切回原 thread 时会重新出现。
 * 2. 刷新页面会丢失所有告警——这是显式契约：UI-only。
 * 3. 告警永远不会被任何 backend API 带上，也不会进入 `threadMessages` / `agentApi.*` 调用。
 */
let _sharedAlerts = null

const _generateId = () => {
  // 不依赖 crypto.randomUUID（部分测试环境缺失），简单够用
  return `alert-${Date.now()}-${Math.floor(Math.random() * 1e6)}`
}

const _ensureShared = () => {
  if (!_sharedAlerts) {
    _sharedAlerts = reactive({})
  }
  return _sharedAlerts
}

export function useThreadAlerts() {
  const threadAlerts = _ensureShared()

  /**
   * 往指定 thread 推一条告警。category 必须来自 `categorizeChatError` 的返回结构。
   * 重复同 kind 的告警会被去重，避免配额刷新后短时间内连续刷出多条。
   *
   * @param {string} threadId
   * @param {{ kind: string, httpStatus?: number, title: string, body: object }|null|undefined} category
   * @returns {string|null} 新告警 id；category 为空时返回 null
   */
  const pushThreadAlert = (threadId, category) => {
    if (!threadId || !category) return null
    if (!threadAlerts[threadId]) {
      threadAlerts[threadId] = []
    }
    const list = threadAlerts[threadId]
    // 同 kind 已有告警且 createdAt 在 5s 内 → 不重复入栈
    const now = Date.now()
    const recentSame = list.find((a) => a.kind === category.kind && now - (a.createdAt || 0) < 5000)
    if (recentSame) return recentSame.id

    const alert = {
      id: _generateId(),
      kind: category.kind,
      title: category.title,
      body: category.body,
      httpStatus: category.httpStatus,
      createdAt: now
    }
    list.push(alert)
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
    return list.length
  }

  /**
   * 清空某 thread 的所有告警（一般用于 thread 被删除时）。
   */
  const clearThreadAlerts = (threadId) => {
    if (!threadId) return
    delete threadAlerts[threadId]
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
   */
  const pushAlertFromError = (threadId, error) => {
    const category = categorizeChatError(error)
    return pushThreadAlert(threadId, category)
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
