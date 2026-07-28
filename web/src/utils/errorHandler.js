import { message } from 'ant-design-vue'

/**
 * 统一错误处理工具类
 */
export class ErrorHandler {
  /**
   * 处理通用错误
   * @param {Error} error - 错误对象
   * @param {string} context - 错误上下文
   * @param {Object} options - 配置选项
   */
  static handleError(error, context = '操作', options = {}) {
    const {
      showMessage = true,
      logToConsole = true,
      customMessage = null,
      severity = 'error'
    } = options

    // 控制台日志
    if (logToConsole) {
      console.error(`${context}失败:`, error)
    }

    // 用户提示
    if (showMessage) {
      const displayMessage = customMessage || this.getErrorMessage(error, context)

      switch (severity) {
        case 'warning':
          message.warning(displayMessage)
          break
        case 'info':
          message.info(displayMessage)
          break
        case 'error':
        default:
          message.error(displayMessage)
          break
      }
    }

    return error
  }

  /**
   * 获取错误消息
   * @param {Error} error - 错误对象
   * @param {string} context - 错误上下文
   * @returns {string} 错误消息
   */
  static getErrorMessage(error, context) {
    if (error?.message) {
      return `${context}失败: ${error.message}`
    }
    return `${context}失败`
  }

  /**
   * 处理网络请求错误
   * @param {Error} error - 错误对象
   * @param {string} context - 错误上下文
   */
  static handleNetworkError(error, context = '网络请求') {
    let customMessage = null

    if (error?.code === 'NETWORK_ERROR') {
      customMessage = '网络连接失败，请检查网络设置'
    } else if (error?.status === 401) {
      customMessage = '认证失败，请重新登录'
    } else if (error?.status === 403) {
      customMessage = '权限不足，无法执行此操作'
    } else if (error?.status === 404) {
      customMessage = '请求的资源不存在'
    } else if (error?.status >= 500) {
      customMessage = '服务器错误，请稍后重试'
    }

    return this.handleError(error, context, { customMessage })
  }

  /**
   * 处理聊天相关错误
   * @param {Error} error - 错误对象
   * @param {string} operation - 操作类型
   */
  static handleChatError(error, operation) {
    const contextMap = {
      send: '发送消息',
      create: '创建对话',
      delete: '删除对话',
      rename: '重命名对话',
      load: '加载对话',
      export: '导出对话',
      stream: '流式处理'
    }

    const context = contextMap[operation] || operation
    return this.handleError(error, context)
  }

  /**
   * 处理验证错误
   * @param {string} message - 验证错误消息
   */
  static handleValidationError(message) {
    return this.handleError(new Error(message), '输入验证', {
      severity: 'warning',
      customMessage: message
    })
  }

  /**
   * 处理异步操作错误
   * @param {Function} asyncFn - 异步函数
   * @param {string} context - 错误上下文
   * @param {Object} options - 配置选项
   */
  static async handleAsync(asyncFn, context, options = {}) {
    try {
      return await asyncFn()
    } catch (error) {
      this.handleError(error, context, options)
      throw error
    }
  }

  /**
   * 创建错误处理装饰器
   * @param {string} context - 错误上下文
   * @param {Object} options - 配置选项
   */
  static createHandler(context, options = {}) {
    return (error) => this.handleError(error, context, options)
  }
}

/**
 * 快捷方法
 */
export const handleChatError = ErrorHandler.handleChatError.bind(ErrorHandler)
export const handleNetworkError = ErrorHandler.handleNetworkError.bind(ErrorHandler)
export const handleValidationError = ErrorHandler.handleValidationError.bind(ErrorHandler)
export const handleAsync = ErrorHandler.handleAsync.bind(ErrorHandler)

/**
 * 将一个 Error / SSE 事件归类为可内联在会话流中的告警类型。
 *
 * 设计目标：调用方拿到一个标准化的 { kind, httpStatus?, title, body } 对象，
 * 然后由 UI 层决定是 pushThreadAlert 内联显示，还是回退到 toast。
 *
 * kind 取值：
 * - 'quota_exceeded'   后端 429 + body.detail.code === 'token_quota_exceeded'
 * - 'permission_denied' 后端 403，或 message 含 "apikey.invoke" / "缺少权限"
 * - 'generic_error'    其他 4xx/5xx
 * - null               不是 Error 对象 / 没有可分类的 HTTP 信息（如 SSE 主动 abort）
 *
 * @param {Error|Object|null|undefined} error
 * @returns {{ kind: string, httpStatus?: number, title: string, body: object } | null}
 */
export function categorizeChatError(error) {
  if (!error) return null

  // 兼容 SSE event：useAgentRunStream 那边可能传过来一个 plain object
  const isSseErrorEvent = typeof error === 'object' && 'payload' in error
  const httpStatus = isSseErrorEvent ? error?.status : (error?.response?.status ?? error?.status)

  const detail = isSseErrorEvent
    ? error?.payload?.chunk || error?.payload || {}
    : (error?.response?.data?.detail ?? error?.response?.data ?? {})

  // quota_exceeded 优先识别（HTTP 429 + 结构化 code）
  const isQuotaExceeded =
    (httpStatus === 429 && detail?.code === 'token_quota_exceeded') ||
    (isSseErrorEvent && detail?.error_type === 'token_quota_exceeded')

  if (isQuotaExceeded) {
    const quota = Number.isFinite(Number(detail?.quota)) ? Number(detail.quota) : null
    const used = Number.isFinite(Number(detail?.used)) ? Number(detail.used) : null
    const remaining = Number.isFinite(Number(detail?.remaining)) ? Number(detail.remaining) : null
    const resetAt = typeof detail?.reset_at === 'string' ? detail.reset_at : null
    const rawMessage =
      detail?.message || (isSseErrorEvent ? detail?.error_message : null) || '本周 token 额度已用尽'
    return {
      kind: 'quota_exceeded',
      httpStatus: 429,
      title: 'Token 额度已用尽',
      body: {
        message: rawMessage,
        quota,
        used,
        remaining,
        resetAt
      }
    }
  }

  const errorMessage = isSseErrorEvent
    ? detail?.error_message || detail?.message || ''
    : error?.message || ''

  // 部分后端在 detail 里直接放可读字符串（base.js 也会同步到 error.message），
  // 但也见过 detail 是对象、message 字段缺失的情况——把 detail 自身的 message 也纳入匹配
  const detailMessage =
    !isSseErrorEvent && detail && typeof detail === 'object' ? detail?.message || '' : ''
  const combinedMessage = [errorMessage, detailMessage].filter(Boolean).join(' ')

  // 权限相关：HTTP 403 或错误文案中提到权限 / apikey.invoke
  if (
    httpStatus === 403 ||
    /缺少权限[:：]/.test(combinedMessage) ||
    /apikey\.invoke/.test(combinedMessage) ||
    /无权/.test(combinedMessage)
  ) {
    return {
      kind: 'permission_denied',
      httpStatus: httpStatus || 403,
      title: '权限不足',
      body: {
        message: errorMessage || detailMessage || '当前用户没有执行此操作的权限',
        hint: /apikey\.invoke/.test(combinedMessage)
          ? 'API Key 鉴权需要用户拥有 apikey.invoke 权限，请联系管理员在权限管理页授权。'
          : null
      }
    }
  }

  // 其余 4xx / 5xx 归为 generic_error
  if (httpStatus && httpStatus >= 400) {
    return {
      kind: 'generic_error',
      httpStatus,
      title: `请求失败 (${httpStatus})`,
      body: {
        message: errorMessage || detailMessage || '请稍后重试'
      }
    }
  }

  // Run/SSE 错误不一定带 HTTP 状态；只要后端给出了结构化 error_type，
  // 就按会话内错误展示。正文仅使用后端已经脱敏的用户文案。
  if (isSseErrorEvent && detail?.error_type) {
    return {
      kind: 'generic_error',
      title: detail.error_type === 'agent_execution_error' ? '智能体运行失败' : '请求失败',
      body: {
        message: errorMessage || '请稍后重试'
      }
    }
  }

  // 裸 Error（没 HTTP 上下文）一般来自前端逻辑，不该内联
  return null
}

export default ErrorHandler
