import test from 'node:test'
import assert from 'node:assert/strict'
import { createServer } from 'vite'

test('processBrowserSseResponse 解析 data 行', async () => {
  const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
  try {
    const { processBrowserSseResponse } = await server.ssrLoadModule('/src/composables/useBrowserStream.js')

    const encoder = new TextEncoder()
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode('data: {"ts":1,"screenshot_b64":"abc","status":"active"}\n\n'))
        controller.enqueue(encoder.encode('data: {"status":"unavailable"}\n\n'))
        controller.close()
      }
    })

    const events = []
    await processBrowserSseResponse({ body: stream }, (payload) => {
      events.push(payload)
    })

    assert.equal(events.length, 2)
    assert.equal(events[0].screenshot_b64, 'abc')
    assert.equal(events[0].status, 'active')
    assert.equal(events[1].status, 'unavailable')
  } finally {
    await server.close()
  }
})

test('processBrowserSseResponse 忽略非 data 行', async () => {
  const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
  try {
    const { processBrowserSseResponse } = await server.ssrLoadModule('/src/composables/useBrowserStream.js')
    const encoder = new TextEncoder()
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(encoder.encode(':heartbeat\n\ndata: {"status":"active"}\n\nevent: bye\n\n'))
        controller.close()
      }
    })
    const events = []
    await processBrowserSseResponse({ body: stream }, (p) => events.push(p))
    assert.equal(events.length, 1)
    assert.equal(events[0].status, 'active')
  } finally {
    await server.close()
  }
})