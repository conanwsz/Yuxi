/**
 * /minio/public/ 反代的路径安全重写。
 *
 * 背景 (vuln-0007):
 *   生产环境 /minio/public/ 反代仅在原始未归一化 URI 上做前缀白名单校验,
 *   而 .. 点段由下游 MinIO (Go http.ServeMux) 才折叠,导致原始路径上的安全
 *   判断与最终交给上游的路径表示不一致,出现"原始路径判定、归一化路径执行"
 *   的语义错位。攻击者可构造 /minio/public/../<bucket>/ 逃出 public 桶限制,
 *   与未修改的 MinIO 默认 root 凭证串联后已实测读取到企业内部知识库。
 *
 * 修复策略:
 *   切割 query → 逐段反复解码 (最多 3 轮,消除双重编码) → 折叠 . / .. →
 *   在归一化结果上重新断言 /public/ 前缀 → 任何越界请求一律映射到
 *   不存在的键。段内反斜杠 / 空字符 / 非法编码一律拒绝。
 *   发射给上游的段保持原始百分号编码,避免破坏含中文与空格的真实对象键
 *   (例如 kb_xxx/upload/设备维修经验库_RAG 格式_xxx.md)。
 *
 * 用法 (vite.config.js):
 *   import { minioPublicRewrite } from '@/utils/minioPublicRewrite'
 *   proxy: { '^/minio/public/': { ..., rewrite: minioPublicRewrite } }
 */

/** 越界请求统一映射到的目标键,MinIO 上不存在,等价于 NoSuchKey。 */
const BLOCKED_TARGET = '/public/__blocked__'

/** 单段最多解码轮次,3 轮可消除 %252e%252e 这类双重编码,再高则无意义。 */
const MAX_DECODE_ROUNDS = 3

/**
 * 逐段解码,直至稳定或达到轮次上限。解码异常 (如 %ZZ) 视为攻击信号,
 * 返回 null 让调用方拒绝该请求。
 */
const decodeUpToStable = (seg) => {
  let cur = seg
  for (let i = 0; i < MAX_DECODE_ROUNDS; i += 1) {
    let next
    try {
      next = decodeURIComponent(cur)
    } catch {
      return null
    }
    if (next === cur) return cur
    cur = next
  }
  return cur
}

export const minioPublicRewrite = (path) => {
  const qIdx = path.indexOf('?')
  const rawPath = qIdx === -1 ? path : path.slice(0, qIdx)
  const query = qIdx === -1 ? '' : path.slice(qIdx)

  const out = []
  for (const seg of rawPath.split('/')) {
    const decoded = decodeUpToStable(seg)
    if (decoded === null) return BLOCKED_TARGET + query
    if (decoded === '.') continue
    if (decoded === '..') {
      // 归一化过程中 .. 弹栈若已弹空,结果不再以 /public 开头,落入 blocked
      if (!out.length) return BLOCKED_TARGET + query
      out.pop()
      continue
    }
    // 段内混入反斜杠或 NUL 视为攻击信号 (Windows 路径混淆 / 字符串截断)
    if (/[\\/]/.test(decoded) || decoded.includes('\0')) {
      return BLOCKED_TARGET + query
    }
    // 发射原始段,保留百分号编码以兼容含中文与空格的真实对象键
    out.push(seg)
  }

  let normalized = out.join('/') || '/'
  if (normalized.startsWith('/minio/')) {
    normalized = normalized.slice('/minio'.length)
  }
  if (normalized !== '/public' && !normalized.startsWith('/public/')) {
    return BLOCKED_TARGET + query
  }
  return normalized + query
}