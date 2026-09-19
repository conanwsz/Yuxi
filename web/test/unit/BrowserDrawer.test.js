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

test('BrowserDrawer 组件能被加载', async () => {
  const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
  try {
    const mod = await server.ssrLoadModule('/src/components/BrowserDrawer.vue')
    assert.ok(mod, 'BrowserDrawer 模块应能加载')
    assert.equal(typeof mod.default, 'object', 'BrowserDrawer 应导出默认组件对象')
  } finally {
    await server.close()
  }
})

test('browserDrawerStore setEnabled(false) 自动 hide 抽屉', async () => {
  const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
  try {
    setActivePinia(createPinia())
    const { useBrowserDrawerStore } = await server.ssrLoadModule('/src/stores/browserDrawer.js')
    const store = useBrowserDrawerStore()

    store.show()
    assert.equal(store.visible, true)

    store.setEnabled(false)
    assert.equal(store.enabled, false)
    assert.equal(store.visible, false, 'setEnabled(false) 应自动 hide')
  } finally {
    await server.close()
  }
})

test('browserDrawerStore 状态变更通过 setLastEvent 推 serviceStatus', async () => {
  const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
  try {
    setActivePinia(createPinia())
    const { useBrowserDrawerStore } = await server.ssrLoadModule('/src/stores/browserDrawer.js')
    const store = useBrowserDrawerStore()

    store.setLastEvent({ status: 'active', screenshot_b64: 'png-bytes' })
    assert.equal(store.serviceStatus, 'active')

    store.setLastEvent({ status: 'unavailable' })
    assert.equal(store.serviceStatus, 'unavailable')
  } finally {
    await server.close()
  }
})