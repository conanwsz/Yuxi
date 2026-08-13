import { computed, reactive, ref } from 'vue'
import { defineStore } from 'pinia'
import { agentApi, threadApi } from '@/apis'
import { handleChatError } from '@/utils/errorHandler'

const PAGE_SIZE = 100

export const useChatThreadsStore = defineStore('chatThreads', () => {
  const threads = ref([])
  const currentThreadId = ref(null)
  const hasMoreThreads = ref(true)
  const isLoadingMoreThreads = ref(false)

  // 正在输出的会话（run 进行中），用于侧边栏标题闪烁
  const streamingThreadIds = reactive(new Set())
  // 有未读回复的会话（非当前会话收到回复完成），用于侧边栏蓝色圆点
  const unreadThreadIds = reactive(new Set())

  const currentThread = computed(() => {
    if (!currentThreadId.value) return null
    return threads.value.find((thread) => thread.id === currentThreadId.value) || null
  })

  const setCurrentThreadId = (threadId) => {
    currentThreadId.value = threadId || null
  }

  const upsertThread = (thread) => {
    if (!thread?.id) return
    const index = threads.value.findIndex((item) => item.id === thread.id)
    if (index >= 0) {
      threads.value[index] = { ...threads.value[index], ...thread }
      return
    }
    threads.value = [thread, ...threads.value]
  }

  const loadThreads = async (agentId = null) => {
    try {
      const fetchedThreads = await threadApi.getThreads(agentId, PAGE_SIZE, 0)
      threads.value = fetchedThreads || []
      hasMoreThreads.value = Boolean(fetchedThreads && fetchedThreads.length >= PAGE_SIZE)
      if (
        currentThreadId.value &&
        !threads.value.find((thread) => thread.id === currentThreadId.value)
      ) {
        currentThreadId.value = null
      }
      return threads.value
    } catch (error) {
      console.error('Failed to fetch threads:', error)
      handleChatError(error, 'fetch')
      throw error
    }
  }

  const loadMoreThreads = async (agentId = null) => {
    if (isLoadingMoreThreads.value || !hasMoreThreads.value) return

    isLoadingMoreThreads.value = true
    try {
      const fetchedThreads = await threadApi.getThreads(agentId, PAGE_SIZE, threads.value.length)
      if (fetchedThreads && fetchedThreads.length > 0) {
        // 后端分页会重复返回置顶项，这里只追加列表中尚不存在的线程。
        const existingIds = new Set(threads.value.map((thread) => thread.id))
        const newThreads = fetchedThreads.filter((thread) => !existingIds.has(thread.id))
        threads.value = [...threads.value, ...newThreads]
        hasMoreThreads.value = newThreads.length >= PAGE_SIZE
      } else {
        hasMoreThreads.value = false
      }
    } catch (error) {
      console.error('Failed to load more chats:', error)
      handleChatError(error, 'fetch')
    } finally {
      isLoadingMoreThreads.value = false
    }
  }

  // 只更新已存在 thread 的 updated_at，不插入新项。
  // 用于发送消息 / 收到回复后让会话在列表中上浮，避免 upsertThread 误插入子智能体线程。
  const touchThread = (threadId) => {
    const thread = threads.value.find((item) => item.id === threadId)
    if (thread) {
      thread.updated_at = new Date().toISOString()
    }
  }

  const markUnread = (threadId) => {
    if (threadId && threadId !== currentThreadId.value) {
      unreadThreadIds.add(threadId)
    }
  }

  const clearUnread = (threadId) => {
    if (threadId) {
      unreadThreadIds.delete(threadId)
    }
  }

  // 轮询活跃 run 只负责侧边栏的运行、跳顶和未读状态；请求队列状态由会话内 SSE 独立维护。
  let previousActiveRunMap = new Map()

  const pollActiveRuns = async () => {
    try {
      const response = await agentApi.getActiveRuns()
      const runs = response?.runs || []
      const currentMap = new Map(runs.map((run) => [run.run_id, run.thread_id]))

      for (const [runId, threadId] of currentMap) {
        if (!previousActiveRunMap.has(runId)) {
          streamingThreadIds.add(threadId)
        }
      }

      for (const [runId, threadId] of previousActiveRunMap) {
        if (!currentMap.has(runId)) {
          streamingThreadIds.delete(threadId)
          touchThread(threadId)
          markUnread(threadId)
        }
      }

      previousActiveRunMap = currentMap
    } catch {
      // 轮询失败静默，下次重试。
    }
  }

  const createThread = async (agentId, title = '新的对话', metadata = {}) => {
    if (!agentId) return null

    try {
      const thread = await threadApi.createThread(agentId, title, metadata)
      if (thread) {
        threads.value = [thread, ...threads.value.filter((item) => item.id !== thread.id)]
      }
      return thread
    } catch (error) {
      console.error('Failed to create thread:', error)
      handleChatError(error, 'create')
      throw error
    }
  }

  const deleteThread = async (threadId) => {
    if (!threadId) return

    try {
      await threadApi.deleteThread(threadId)
      threads.value = threads.value.filter((thread) => thread.id !== threadId)
      if (currentThreadId.value === threadId) {
        currentThreadId.value = null
      }
    } catch (error) {
      console.error('Failed to delete thread:', error)
      handleChatError(error, 'delete')
      throw error
    }
  }

  const updateThread = async (threadId, title, isPinned, toolApprovalMode) => {
    if (!threadId) return

    const normalizedTitle = title ? String(title).replace(/\s+/g, ' ').trim().slice(0, 255) : null
    if (title && !normalizedTitle) return
    if (!normalizedTitle && isPinned === undefined && toolApprovalMode === undefined) return

    try {
      const updatedThread = await threadApi.updateThread(
        threadId,
        normalizedTitle,
        isPinned,
        toolApprovalMode
      )
      upsertThread(updatedThread)
      return updatedThread
    } catch (error) {
      console.error('Failed to update thread:', error)
      handleChatError(error, 'update')
      throw error
    }
  }

  return {
    threads,
    currentThreadId,
    currentThread,
    hasMoreThreads,
    isLoadingMoreThreads,
    streamingThreadIds,
    unreadThreadIds,
    setCurrentThreadId,
    upsertThread,
    touchThread,
    markUnread,
    clearUnread,
    pollActiveRuns,
    loadThreads,
    loadMoreThreads,
    createThread,
    deleteThread,
    updateThread
  }
})
