import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import { createPinia, setActivePinia } from 'pinia'
import { createServer } from 'vite'

let server
let useUserStore
let originalFetch

before(async () => {
  const storage = new Map()
  globalThis.localStorage = {
    getItem: (key) => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, String(value)),
    removeItem: (key) => storage.delete(key)
  }
  originalFetch = globalThis.fetch
  server = await createServer({ server: { middlewareMode: true }, appType: 'custom' })
  ;({ useUserStore } = await server.ssrLoadModule('/src/stores/user.js'))
})

after(async () => {
  globalThis.fetch = originalFetch
  delete globalThis.localStorage
  await server?.close()
})

test('登录会随表单提交服务端签发的滑动验证结果', async () => {
  setActivePinia(createPinia())
  const requests = []
  globalThis.fetch = async (url, options = {}) => {
    requests.push({ url, options })
    if (url === '/api/auth/token') {
      return new Response(JSON.stringify({ access_token: 'token' }), { status: 200 })
    }
    if (url === '/api/auth/me') {
      return new Response(
        JSON.stringify({ id: 1, username: '测试用户', uid: 'tester', role: 'user', permissions: [] }),
        { status: 200 }
      )
    }
    throw new Error(`Unexpected request: ${url}`)
  }

  await useUserStore().login({
    loginId: 'tester',
    password: 'password',
    captchaToken: 'signed-captcha-token',
    captchaOffset: 88
  })

  const loginRequest = requests.find((request) => request.url === '/api/auth/token')
  assert.equal(loginRequest.options.body.get('captcha_token'), 'signed-captcha-token')
  assert.equal(loginRequest.options.body.get('captcha_offset'), '88')
  assert.equal(loginRequest.options.body.get('captcha_offset_y'), null)
})

test('登录风控响应会保留滑动验证标识', async () => {
  setActivePinia(createPinia())
  globalThis.fetch = async () =>
    new Response(JSON.stringify({ detail: '请完成滑动验证码后再登录' }), {
      status: 401,
      headers: { 'X-Captcha-Required': 'true' }
    })

  await assert.rejects(
    useUserStore().login({ loginId: 'tester', password: 'password' }),
    (error) => error.status === 401 && error.captchaRequired === true
  )
})
