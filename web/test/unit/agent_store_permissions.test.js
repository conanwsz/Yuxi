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

test('只读 Agent 选择时仅加载安全运行元数据', async () => {
  const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })

  try {
    setActivePinia(createPinia())
    const { agentApi } = await server.ssrLoadModule('/src/apis/agent_api.js')
    const { useAgentStore } = await server.ssrLoadModule('/src/stores/agent.js')
    const requests = []

    agentApi.getAgents = async () => ({
      agents: [{ slug: 'assigned-agent', name: 'Assigned', can_view_config: false }]
    })
    agentApi.getAgentDetail = async () => {
      requests.push('detail')
      throw new Error('不应请求完整详情')
    }
    agentApi.getAgentRuntimeMetadata = async () => {
      requests.push('runtime')
      return {
        runtime_context: { model: 'provider:model', knowledges: ['kb-1'] },
        configurable_items: { knowledges: { kind: 'knowledges', options: [] } }
      }
    }

    const store = useAgentStore()
    await store.fetchAgents()
    await store.selectAgent('assigned-agent')

    assert.deepEqual(requests, ['runtime'])
    assert.equal(store.agentConfig.model, 'provider:model')
    assert.equal(store.agentConfig.system_prompt, undefined)
  } finally {
    await server.close()
  }
})

test('可管理 Agent 选择时加载完整管理详情', async () => {
  const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })

  try {
    setActivePinia(createPinia())
    const { agentApi } = await server.ssrLoadModule('/src/apis/agent_api.js')
    const { useAgentStore } = await server.ssrLoadModule('/src/stores/agent.js')
    const requests = []

    agentApi.getAgents = async () => ({
      agents: [{ slug: 'owned-agent', name: 'Owned', can_view_config: true }]
    })
    agentApi.getAgentDetail = async () => {
      requests.push('detail')
      return {
        agent: {
          slug: 'owned-agent',
          name: 'Owned',
          can_view_config: true,
          config_json: { context: { system_prompt: 'owner-visible' } },
          configurable_items: {}
        }
      }
    }
    agentApi.getAgentRuntimeMetadata = async () => {
      requests.push('runtime')
      return {}
    }

    const store = useAgentStore()
    await store.fetchAgents()
    await store.selectAgent('owned-agent')

    assert.deepEqual(requests, ['detail'])
    assert.equal(store.agentConfig.system_prompt, 'owner-visible')
  } finally {
    await server.close()
  }
})
