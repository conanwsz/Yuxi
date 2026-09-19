import assert from 'node:assert/strict'
import test from 'node:test'

import { minioPublicRewrite } from '../../src/utils/minioPublicRewrite.js'

// vuln-0007: 越界请求统一被 rewrite 重写到的不存在键,等价于 NoSuchKey。
// 这里直接以字面量引用,避免对外多暴露一个模块内部常量。
const BLOCKED = '/public/__blocked__'
const rewrite = minioPublicRewrite

test('合法公开桶路径原样转发并保留查询串', () => {
  assert.equal(rewrite('/minio/public/images/22041001/uuid.png'), '/public/images/22041001/uuid.png')
  assert.equal(rewrite('/minio/public/x.png?t=1'), '/public/x.png?t=1')
  assert.equal(rewrite('/minio/public/?list-type=2'), '/public/?list-type=2')
})

test('多级嵌套与单点段 (./) 折叠后仍落在 /public 内', () => {
  assert.equal(rewrite('/minio/public/a/b/c.md'), '/public/a/b/c.md')
  assert.equal(rewrite('/minio/public/./a/b'), '/public/a/b')
  assert.equal(rewrite('/minio/public/a/./b/./c'), '/public/a/b/c')
})

test('含中文与空格的对象键发射原始编码段,不破坏业务键', () => {
  // 真实键: kb_1nj6x50e4l/upload/设备维修经验库_RAG 格式_1784703534821.md
  const path = '/minio/public/kb_1nj6x50e4l/upload/%E8%AE%BE%E5%A4%87%E7%BB%B4%E4%BF%AE%E7%BB%8F%E9%AA%8C%E5%BA%93_RAG%20%E6%A0%BC%E5%BC%8F_1784703534821.md'
  assert.equal(
    rewrite(path),
    '/public/kb_1nj6x50e4l/upload/%E8%AE%BE%E5%A4%87%E7%BB%B4%E4%BF%AE%E7%BB%8F%E9%AA%8C%E5%BA%93_RAG%20%E6%A0%BC%E5%BC%8F_1784703534821.md'
  )
})

test('.. 点段逃逸: 弹出一段 (跨桶) 一律落入 blocked', () => {
  assert.equal(rewrite('/minio/public/../knowledgebases/'), BLOCKED)
  assert.equal(rewrite('/minio/public/../_minio/admin/'), BLOCKED)
  assert.equal(rewrite('/minio/public/../.minio.sys/config/'), BLOCKED)
  assert.equal(rewrite('/minio/public/a/../../'), BLOCKED)
})

test('.. 弹栈弹空 (越界跳出 /public) 一律落入 blocked', () => {
  assert.equal(rewrite('/minio/public/../'), BLOCKED)
  assert.equal(rewrite('/minio/public/../../etc'), BLOCKED)
  assert.equal(rewrite('/minio/public/a/../../../b'), BLOCKED)
})

test('编码点段 (%2e%2e) 经解码后按 .. 处理', () => {
  // 跨桶: 弹出 public 段后剩余桶名不在 /public 内 → blocked
  assert.equal(rewrite('/minio/public/%2e%2e/knowledgebases/'), BLOCKED)
  assert.equal(rewrite('/minio/public/%2E%2E/_minio/admin/'), BLOCKED)
  // 同桶内相对路径: 弹出 a 后 b 仍在 /public 内 → 合法转发 (无安全风险)
  assert.equal(rewrite('/minio/public/a/%2e%2e/b'), '/public/b')
})

test('编码斜杠 (%2f, %5c) 在段内出现视为攻击信号', () => {
  // %2f 解码后是 /,会在段内引入额外分隔,落到目标桶之外 → blocked
  assert.equal(rewrite('/minio/public/a%2fb/c'), BLOCKED)
  // %5c 解码后是 \,显式拒绝避免 Windows 路径混淆
  assert.equal(rewrite('/minio/public/a%5cb/c'), BLOCKED)
})

test('双重编码 (%252e%252e) 经多轮解码后按 .. 处理', () => {
  // 跨桶双重编码: 三轮解码后仍按 .. 处理,目标桶不在 /public 内 → blocked
  assert.equal(rewrite('/minio/public/%252e%252e/knowledgebases/'), BLOCKED)
  // 同桶内弹出合法
  assert.equal(rewrite('/minio/public/a/%252e%252e/b'), '/public/b')
})

test('..%2f / %2e%2e%2f 等混合编码段最终仍被判为越界', () => {
  assert.equal(rewrite('/minio/public/..%2fknowledgebases/'), BLOCKED)
  assert.equal(rewrite('/minio/public/a/..%2fb'), BLOCKED)
  assert.equal(rewrite('/minio/public/%2e%2e%2fknowledgebases'), BLOCKED)
})

test('非法编码段 (%ZZ) 直接拒绝', () => {
  assert.equal(rewrite('/minio/public/%ZZ/x'), BLOCKED)
  assert.equal(rewrite('/minio/public/a/%GG'), BLOCKED)
})

test('查询串透传但不救越界: 越界时仍落入 blocked,query 原样保留', () => {
  assert.equal(rewrite('/minio/public/../x?list-type=2'), `${BLOCKED}?list-type=2`)
  assert.equal(
    rewrite('/minio/public/%2e%2e/x?list-type=2&max-keys=1000'),
    `${BLOCKED}?list-type=2&max-keys=1000`
  )
})

test('root path /minio/public/ 归一化后等于 /public/,正常转发', () => {
  assert.equal(rewrite('/minio/public/'), '/public/')
  // 不带末尾斜杠的输入也保留该语义 (MinIO 上 /public 与 /public/ 等价,这里不强行加 /)
  assert.equal(rewrite('/minio/public'), '/public')
})

test('不匹配前缀 (例如 /minio/private/) 不在本 rewrite 职责范围,防御性落入 blocked', () => {
  // vite http-proxy-middleware 只把 '^/minio/public/' 命中的请求交给本 rewrite,
  // 但若被错误调用 (例如配置被改),本函数仍然按"非 /public 前缀则拒绝"处理,
  // 不放行任何 /minio/* 路径绕过 /public 限制。这是 belt-and-suspenders 行为。
  assert.equal(rewrite('/minio/private/foo'), BLOCKED)
})