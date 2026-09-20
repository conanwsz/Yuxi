import test from 'node:test'
import assert from 'node:assert/strict'
import { createServer } from 'vite'

const storageValues = new Map()
globalThis.localStorage = {
  getItem: (key) => storageValues.get(key) ?? null,
  setItem: (key, value) => storageValues.set(key, String(value)),
  removeItem: (key) => storageValues.delete(key),
  clear: () => storageValues.clear()
}

test('BrowserStateSection 组件能被正确加载', async () => {
  const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
  try {
    const mod = await server.ssrLoadModule('/src/components/BrowserStateSection.vue')
    assert.ok(mod, 'BrowserStateSection 模块应能加载')
    assert.equal(typeof mod.default, 'object', 'BrowserStateSection 应导出默认组件对象')
  } finally {
    await server.close()
  }
})
