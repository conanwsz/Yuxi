import assert from 'node:assert/strict'
import test from 'node:test'

import { createPinia, setActivePinia } from 'pinia'
import { createServer } from 'vite'

const storageValues = new Map()
globalThis.localStorage = {
  getItem: (key) => storageValues.get(key) ?? null,
  setItem: (key, value) => storageValues.set(key, String(value)),
  removeItem: (key) => storageValues.delete(key),
  clear: () => storageValues.clear()
}

test('系统配置自动保存会等待服务端确认并返回保存结果', async () => {
  const server = await createServer({
    server: { middlewareMode: true },
    appType: 'custom'
  })

  try {
    setActivePinia(createPinia())
    const { configApi } = await server.ssrLoadModule('/src/apis/system_api.js')
    const { useConfigStore } = await server.ssrLoadModule('/src/stores/config.js')
    const requests = []

    configApi.updateConfigBatch = async (items) => {
      requests.push(items)
      return { default_model: items.default_model, fast_model: 'provider:fast' }
    }

    const store = useConfigStore()
    store.config = { default_model: 'provider:old', fast_model: 'provider:fast' }

    const result = await store.setConfigValue('default_model', 'provider:new')

    assert.equal(result, true)
    assert.deepEqual(requests, [{ default_model: 'provider:new' }])
    assert.equal(store.config.default_model, 'provider:new')
  } finally {
    await server.close()
  }
})

test('系统配置保存失败会恢复原值并返回失败结果', async () => {
  const server = await createServer({
    server: { middlewareMode: true },
    appType: 'custom'
  })

  try {
    setActivePinia(createPinia())
    const { configApi } = await server.ssrLoadModule('/src/apis/system_api.js')
    const { useConfigStore } = await server.ssrLoadModule('/src/stores/config.js')

    configApi.updateConfigBatch = async () => {
      throw new Error('保存失败')
    }

    const store = useConfigStore()
    store.config = { default_model: 'provider:old' }

    const result = await store.setConfigValue('default_model', 'provider:new')

    assert.equal(result, false)
    assert.equal(store.config.default_model, 'provider:old')
  } finally {
    await server.close()
  }
})
