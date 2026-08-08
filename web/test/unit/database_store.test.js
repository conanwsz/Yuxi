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

test('普通用户即使拥有知识库读取权限也只加载可使用知识库', async () => {
  const server = await createServer({
    server: { middlewareMode: true },
    appType: 'custom'
  })

  try {
    setActivePinia(createPinia())
    const { databaseApi } = await server.ssrLoadModule('/src/apis/knowledge_api.js')
    const { useDatabaseStore } = await server.ssrLoadModule('/src/stores/database.js')
    const { useUserStore } = await server.ssrLoadModule('/src/stores/user.js')
    const userStore = useUserStore()
    const requests = []

    userStore.userRole = 'user'
    userStore.permissions = ['knowledge.read']
    databaseApi.getDatabases = async () => {
      requests.push('management')
      return { databases: [] }
    }
    databaseApi.getAccessibleDatabases = async () => {
      requests.push('accessible')
      return { databases: [{ kb_id: 'kb_1', created_at: '2026-08-08T00:00:00Z' }] }
    }

    const store = useDatabaseStore()
    await store.loadDatabases()

    assert.deepEqual(requests, ['accessible'])
    assert.deepEqual(
      store.databases.map((database) => database.kb_id),
      ['kb_1']
    )
  } finally {
    await server.close()
  }
})

test('从二级目录点击全部文件会清空 parent_id 并返回根目录', async () => {
  const server = await createServer({
    server: { middlewareMode: true },
    appType: 'custom'
  })

  try {
    setActivePinia(createPinia())
    const { documentApi } = await server.ssrLoadModule('/src/apis/knowledge_api.js')
    const { useDatabaseStore } = await server.ssrLoadModule('/src/stores/database.js')
    const requests = []

    documentApi.listDocuments = async (kbId, params) => {
      requests.push({ kbId, params })
      return {
        items: [],
        page: 1,
        page_size: 100,
        total: 0,
        has_more: false,
        path_prefix: ''
      }
    }

    const store = useDatabaseStore()
    store.kbId = 'kb_1'
    store.fileBrowser.parentId = 'folder_2'
    store.folderBreadcrumbs = [
      { file_id: null, filename: '全部文件', path_prefix: '' },
      { file_id: 'folder_2', filename: '二级目录', path_prefix: '' }
    ]

    await store.goToFolder(0)

    assert.equal(store.fileBrowser.parentId, null)
    assert.deepEqual(store.folderBreadcrumbs, [
      { file_id: null, filename: '全部文件', path_prefix: '' }
    ])
    assert.deepEqual(requests, [
      {
        kbId: 'kb_1',
        params: {
          page: 1,
          page_size: 100,
          status: 'all',
          recursive: false
        }
      }
    ])
  } finally {
    await server.close()
  }
})
