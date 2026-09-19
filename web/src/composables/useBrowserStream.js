import { ref, watch } from 'vue'

const DEFAULT_BASE = '/api/browser'

/**
 * 订阅 browser-viewer 的 SSE 推流。
 * 与项目其他 SSE composable（如 useAgentRunStream）一致，用 fetch + ReadableStream，
 * 不用 EventSource（项目代码规范）。
 */
export const processBrowserSseResponse = async (response, onEvent) => {
  if (!response || !response.body) return
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() ?? ''
      for (const line of lines) {
        if (line.startsWith('data: ')) {
          const dataText = line.slice('data: '.length)
          try {
            const parsed = JSON.parse(dataText)
            onEvent(parsed)
          } catch (err) {
            console.warn('browser stream parse error', err, dataText)
          }
        }
      }
    }
  } finally {
    reader.releaseLock()
  }
}

export function useBrowserStream(userIdRef) {
  const events = ref([])
  const status = ref('idle') // idle | connecting | active | unavailable
  const reconnectCount = ref(0)
  let stopped = false
  let abortController = null
  let reconnectTimer = null

  function scheduleReconnect() {
    if (stopped) return
    reconnectCount.value++
    const delay = Math.min(30000, 1000 * 2 ** (reconnectCount.value - 1))
    reconnectTimer = setTimeout(connect, delay)
  }

  async function connect() {
    stopped = false
    const userId = userIdRef.value
    if (!userId) return
    status.value = 'connecting'
    abortController = new AbortController()
    try {
      const resp = await fetch(`${DEFAULT_BASE}/stream/${userId}`, {
        signal: abortController.signal,
        headers: { Accept: 'text/event-stream' }
      })
      if (!resp.ok) {
        status.value = 'unavailable'
        scheduleReconnect()
        return
      }
      status.value = 'active'
      await processBrowserSseResponse(resp, (payload) => {
        events.value = [...events.value.slice(-99), payload]
        if (payload.status) status.value = payload.status
        reconnectCount.value = 0
      })
    } catch (err) {
      if (err?.name === 'AbortError') return
      console.warn('browser stream error', err)
      scheduleReconnect()
    }
  }

  function disconnect() {
    stopped = true
    if (reconnectTimer) clearTimeout(reconnectTimer)
    if (abortController) abortController.abort()
    status.value = 'idle'
  }

  watch(userIdRef, () => { disconnect(); connect() })

  return { events, status, reconnectCount, connect, disconnect }
}