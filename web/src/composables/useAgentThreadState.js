const createOnGoingConvState = () => ({
  msgChunks: {},
  currentRequestKey: null,
  currentAssistantKey: null,
  toolCallBuffers: {}
})

const FAILED_HUMAN_MESSAGES_STORAGE_KEY = 'yuxi.failed-human-messages'

const loadFailedHumanMessages = (threadId) => {
  if (typeof sessionStorage === 'undefined') return []
  try {
    const stored = JSON.parse(sessionStorage.getItem(FAILED_HUMAN_MESSAGES_STORAGE_KEY) || '{}')
    return Array.isArray(stored?.[threadId]) ? stored[threadId] : []
  } catch {
    return []
  }
}

const persistFailedHumanMessages = (threadId, messages) => {
  if (!threadId || typeof sessionStorage === 'undefined') return
  try {
    const stored = JSON.parse(sessionStorage.getItem(FAILED_HUMAN_MESSAGES_STORAGE_KEY) || '{}')
    const records = stored && typeof stored === 'object' && !Array.isArray(stored) ? stored : {}
    records[threadId] = messages
    sessionStorage.setItem(FAILED_HUMAN_MESSAGES_STORAGE_KEY, JSON.stringify(records))
  } catch {
    // 本地存储不可用时仍保留当前页面内的消息。
  }
}

const getMessageRequestId = (message) => {
  const requestId = message?.request_id || message?.extra_metadata?.request_id || message?.id
  return typeof requestId === 'string' ? requestId.trim() : ''
}

const getRequestTimestamp = (message) => {
  const match = /^req-(\d{13})-/.exec(getMessageRequestId(message))
  return match ? Number(match[1]) : null
}

const isCompletedReplacement = (failedMessage, serverMessage) => {
  if (serverMessage?.type !== 'human' || serverMessage?.delivery_status !== 'complete') return false

  const failedRequestId = getMessageRequestId(failedMessage)
  const serverRequestId = getMessageRequestId(serverMessage)
  if (failedRequestId && failedRequestId === serverRequestId) return true

  if (String(failedMessage?.content || '').trim() !== String(serverMessage?.content || '').trim()) {
    return false
  }
  const failedAt = getRequestTimestamp(failedMessage)
  const completedAt = getRequestTimestamp(serverMessage)
  return failedAt !== null && completedAt !== null && completedAt >= failedAt
}

const IDLE_QUEUE_SNAPSHOT = Object.freeze({
  status: 'idle',
  paused_reason: null,
  blocking_run_id: null,
  can_continue: false
})

export function useAgentThreadState({
  chatState,
  getCurrentThreadId,
  onStopThread = null,
  onBeforeResetThread = null,
  onBeforeCleanupThread = null
}) {
  const resetThreadUiState = (threadState) => {
    if (!threadState) return
    threadState.replyLoadingVisible = false
    threadState.pendingRequestId = null
  }

  const getThreadState = (threadId) => {
    if (!threadId) return null
    if (!chatState.threadStates[threadId]) {
      chatState.threadStates[threadId] = {
        isStreaming: false,
        runStreamAbortController: null,
        activeRunId: null,
        activeRunSteerable: false,
        runLastSeq: '0-0',
        lastRetryableJobTry: null,
        replyLoadingVisible: false,
        pendingRequestId: null,
        pendingInterrupt: null,
        agentStateRequestVersion: 0,
        // API 在持久化消息前拒绝请求时，保留完整用户消息；队列接纳或历史补齐后再清理。
        failedHumanMessages: loadFailedHumanMessages(threadId),
        onGoingConv: createOnGoingConvState(),
        agentState: null,
        contextCompressing: false,
        queuedRequests: [],
        queueSnapshot: { ...IDLE_QUEUE_SNAPSHOT },
        continueQueueInFlight: false,
        requestStreams: {}
      }
    }
    return chatState.threadStates[threadId]
  }

  const clearFailedHumanMessages = (threadId) => {
    const threadState = getThreadState(threadId)
    if (!threadState?.failedHumanMessages.length) return
    threadState.failedHumanMessages.splice(0)
    persistFailedHumanMessages(threadId, threadState.failedHumanMessages)
  }

  const reconcileFailedHumanMessages = (threadId, serverMessages) => {
    const threadState = getThreadState(threadId)
    if (!threadState?.failedHumanMessages.length || !Array.isArray(serverMessages)) return

    const completedMessages = serverMessages.filter(
      (message) => message?.type === 'human' && message?.delivery_status === 'complete'
    )
    const remaining = threadState.failedHumanMessages.filter(
      (failedMessage) =>
        !completedMessages.some((serverMessage) =>
          isCompletedReplacement(failedMessage, serverMessage)
        )
    )
    if (remaining.length === threadState.failedHumanMessages.length) return

    threadState.failedHumanMessages.splice(0, threadState.failedHumanMessages.length, ...remaining)
    persistFailedHumanMessages(threadId, threadState.failedHumanMessages)
  }

  const stopThreadStream = (threadId) => {
    if (!threadId) return
    if (typeof onStopThread === 'function') {
      onStopThread(threadId)
    }
  }

  const abortAllRequestStreams = (threadState) => {
    if (!threadState?.requestStreams) return
    for (const entry of Object.values(threadState.requestStreams)) {
      entry.controller?.abort()
    }
  }

  const cleanupThreadState = (threadId) => {
    if (!threadId) return
    const threadState = chatState.threadStates[threadId]
    if (!threadState) return

    if (typeof onBeforeCleanupThread === 'function') {
      onBeforeCleanupThread(threadId)
    }

    if (threadState.runStreamAbortController) {
      threadState.runStreamAbortController.abort()
    }
    abortAllRequestStreams(threadState)
    delete chatState.threadStates[threadId]
  }

  const resetOnGoingConv = (
    threadId = null,
    { preserveRunStream = false, preserveRequestStreams = false } = {}
  ) => {
    const targetThreadId =
      threadId || (typeof getCurrentThreadId === 'function' ? getCurrentThreadId() : null)

    if (targetThreadId) {
      const threadState = getThreadState(targetThreadId)
      if (!threadState) return

      if (typeof onBeforeResetThread === 'function') {
        onBeforeResetThread(targetThreadId)
      }

      if (!preserveRunStream && threadState.runStreamAbortController) {
        threadState.runStreamAbortController.abort()
        threadState.runStreamAbortController = null
      }
      if (!preserveRequestStreams && threadState.requestStreams) {
        abortAllRequestStreams(threadState)
        threadState.requestStreams = {}
      }

      threadState.onGoingConv = createOnGoingConvState()
      resetThreadUiState(threadState)
      return
    }

    Object.keys(chatState.threadStates).forEach((id) => {
      cleanupThreadState(id)
    })
  }

  return {
    getThreadState,
    clearFailedHumanMessages,
    reconcileFailedHumanMessages,
    cleanupThreadState,
    resetOnGoingConv,
    stopThreadStream,
    persistFailedHumanMessages
  }
}

export { IDLE_QUEUE_SNAPSHOT }
