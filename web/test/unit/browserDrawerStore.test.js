import test from 'node:test'
import assert from 'node:assert/strict'
import { createPinia, setActivePinia } from 'pinia'
import { createServer } from 'vite'

const storageValues = new Map()
globalThis.localStorage = {
  getItem: (key) => storageValues.get(key) ?? null,
  setItem: (key, value) => storageValues.set(key, String(value)),
  removeItem: (key) => storageValues.delete(key),
  clear: () => storageValues.clear()
}

test('browserDrawer store 默认值', async () => {
  const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
  try {
    setActivePinia(createPinia())
    const { useBrowserDrawerStore } = await server.ssrLoadModule('/src/stores/browserDrawer.js')
    const store = useBrowserDrawerStore()

    assert.equal(store.visible, false)
    assert.equal(store.enabled, true)
    assert.equal(store.width, 480)
    assert.equal(store.serviceStatus, 'idle')
  } finally {
    await server.close()
  }
})

test('setEnabled(false) 关闭并 hide 抽屉', async () => {
  const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
  try {
    setActivePinia(createPinia())
    const { useBrowserDrawerStore } = await server.ssrLoadModule('/src/stores/browserDrawer.js')
    const store = useBrowserDrawerStore()

    store.show()
    assert.equal(store.visible, true)
    store.setEnabled(false)
    assert.equal(store.enabled, false)
    assert.equal(store.visible, false)
  } finally {
    await server.close()
  }
})

test('setWidth 限制在 320-720 之间', async () => {
  const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
  try {
    setActivePinia(createPinia())
    const { useBrowserDrawerStore } = await server.ssrLoadModule('/src/stores/browserDrawer.js')
    const store = useBrowserDrawerStore()

    store.setWidth(100)
    assert.equal(store.width, 320)
    store.setWidth(2000)
    assert.equal(store.width, 720)
    store.setWidth(500)
    assert.equal(store.width, 500)
  } finally {
    await server.close()
  }
})

test('setLastEvent 更新 serviceStatus', async () => {
  const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
  try {
    setActivePinia(createPinia())
    const { useBrowserDrawerStore } = await server.ssrLoadModule('/src/stores/browserDrawer.js')
    const store = useBrowserDrawerStore()

    store.setLastEvent({ status: 'active', screenshot_b64: 'x' })
    assert.equal(store.serviceStatus, 'active')
    assert.equal(store.lastEvent.screenshot_b64, 'x')
  } finally {
    await server.close()
  }
})