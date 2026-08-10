import assert from 'node:assert/strict'
import test from 'node:test'

import { createServer } from 'vite'

test('默认向量知识库显示为知识库', async () => {
  const server = await createServer({
    server: { middlewareMode: true },
    appType: 'custom'
  })

  try {
    const { getKbTypeLabel } = await server.ssrLoadModule('/src/utils/kb_utils.js')

    assert.equal(getKbTypeLabel('milvus'), '知识库')
    assert.equal(getKbTypeLabel('dify'), 'Dify')
  } finally {
    await server.close()
  }
})
