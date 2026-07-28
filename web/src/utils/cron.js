/**
 * 定时任务 cron 表达式与「自然语言控件」双向翻译。
 *
 * 后端存的是 cron（5 段：分 时 日 月 周，服务器 TZ=Asia/Shanghai 解释），
 * 前端 UI 收集用户的"频率类型 + 数字 N + 可选时/分"自然语言配置。
 *
 * UI ↔ DB 边界就在这两个函数上。
 */

const WEEK_LABELS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']

const MONTH_LABELS = [
  '1月',
  '2月',
  '3月',
  '4月',
  '5月',
  '6月',
  '7月',
  '8月',
  '9月',
  '10月',
  '11月',
  '12月'
]

/**
 * 把 cron 5 段字符串拆成对象。任何字段不能识别时返回 null。
 */
export function parseCron(cron) {
  if (typeof cron !== 'string') return null
  const trimmed = cron.trim()
  if (!trimmed) return null
  const parts = trimmed.split(/\s+/)
  if (parts.length !== 5) return null
  const [minute, hour, dom, month, dow] = parts
  return { minute, hour, dom, month, dow, raw: trimmed }
}

/**
 * 把 cron 5 段拆解到 UI 控件的初始值。
 *
 * 返回: { type, value, minute, hour, dayOfWeek, dayOfMonth, label, raw }
 * - type: 'minute' | 'hour' | 'day' | 'week' | 'month' | 'custom'
 * - label: 自然语言显示（如「每 30 分钟」「每天 09:29」）
 * - raw: 原始 cron（type=custom 时不还原）
 */
export function humanizeCron(cron) {
  const parsed = parseCron(cron)
  if (!parsed) {
    return {
      type: 'custom',
      value: 0,
      minute: 0,
      hour: 0,
      dayOfWeek: 1,
      dayOfMonth: 1,
      label: cron || '',
      raw: cron
    }
  }
  const { minute, hour, dom, month, dow } = parsed

  // 每 N 分钟: minute = */N, 其他都是 *
  if (minute.startsWith('*/') && hour === '*' && dom === '*' && month === '*' && dow === '*') {
    const value = parseInt(minute.slice(2), 10) || 1
    return {
      type: 'minute',
      value,
      minute: 0,
      hour: 0,
      dayOfWeek: 1,
      dayOfMonth: 1,
      label: `每 ${value} 分钟`,
      raw: cron
    }
  }

  // 每 N 小时: hour = */N, minute 是具体值
  if (minute !== '*' && hour.startsWith('*/') && dom === '*' && month === '*' && dow === '*') {
    const value = parseInt(hour.slice(2), 10) || 1
    const atMinute = parseInt(minute, 10) || 0
    return {
      type: 'hour',
      value,
      minute: atMinute,
      hour: atMinute, // UI 共用一个分钟字段
      dayOfWeek: 1,
      dayOfMonth: 1,
      label: `每 ${value} 小时 ${String(atMinute).padStart(2, '0')} 分`,
      raw: cron
    }
  }

  // 每天 HH:MM: minute/hour 是具体值，dom/month/dow 都是 *
  if (
    minute !== '*' &&
    hour !== '*' &&
    dom === '*' &&
    month === '*' &&
    dow === '*' &&
    /^\d+$/.test(minute) &&
    /^\d+$/.test(hour)
  ) {
    const h = parseInt(hour, 10)
    const m = parseInt(minute, 10)
    return {
      type: 'day',
      value: 1,
      minute: m,
      hour: h,
      dayOfWeek: 1,
      dayOfMonth: 1,
      label: `每天 ${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`,
      raw: cron
    }
  }

  // 每周 (周 D) HH:MM: minute/hour 具体，dom/month = *，dow 是数字
  if (
    minute !== '*' &&
    hour !== '*' &&
    dom === '*' &&
    month === '*' &&
    /^\d+$/.test(minute) &&
    /^\d+$/.test(hour) &&
    /^\d+$/.test(dow)
  ) {
    const h = parseInt(hour, 10)
    const m = parseInt(minute, 10)
    const dowNum = parseInt(dow, 10) % 7 // cron 周日=0 或 7 都规范化到 0
    return {
      type: 'week',
      value: 1,
      minute: m,
      hour: h,
      dayOfWeek: dowNum,
      dayOfMonth: 1,
      label: `每${WEEK_LABELS[dowNum]} ${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`,
      raw: cron
    }
  }

  // 每月 D 日 HH:MM: minute/hour/dom 具体，month/dow = *
  if (
    minute !== '*' &&
    hour !== '*' &&
    /^\d+$/.test(dom) &&
    month === '*' &&
    dow === '*' &&
    /^\d+$/.test(minute) &&
    /^\d+$/.test(hour)
  ) {
    const h = parseInt(hour, 10)
    const m = parseInt(minute, 10)
    const domNum = parseInt(dom, 10)
    return {
      type: 'month',
      value: 1,
      minute: m,
      hour: h,
      dayOfWeek: 1,
      dayOfMonth: domNum,
      label: `每月 ${domNum} 日 ${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`,
      raw: cron
    }
  }

  // 兜底：复杂 cron 保留原文
  return {
    type: 'custom',
    value: 1,
    minute: 0,
    hour: 0,
    dayOfWeek: 1,
    dayOfMonth: 1,
    label: cron,
    raw: cron
  }
}

/**
 * 把 UI 控件值翻译成 cron 字符串。
 *
 * 输入: { type, value, minute, hour, dayOfWeek, dayOfMonth }
 * - type='minute' → 每 N 分钟（形如 \x2a/N x x x x）
 * - type='hour'   → 每 N 小时 M 分（形如 M \x2a/N x x x）
 * - type='day'    → 每天 M 点 H 分（形如 M H x x x）
 * - type='week'   → 每周 D HH:MM（形如 M H x x D）
 * - type='month'  → 每月 D 日 HH:MM（形如 M H D x x）
 */
export function buildCron({ type, value, minute, hour, dayOfWeek, dayOfMonth }) {
  const m = String(minute ?? 0).padStart(2, '0')
  const h = String(hour ?? 0).padStart(2, '0')
  const v = Math.max(1, parseInt(value, 10) || 1)

  switch (type) {
    case 'minute':
      return `*/${v} * * * *`
    case 'hour':
      return `${m} */${v} * * *`
    case 'day':
      return `${m} ${h} * * *`
    case 'week':
      return `${m} ${h} * * ${dayOfWeek ?? 0}`
    case 'month':
      return `${m} ${h} ${dayOfMonth ?? 1} * *`
    default:
      throw new Error(`不支持的频率类型: ${type}`)
  }
}

/** UI 下拉选项 */
export const FREQUENCY_OPTIONS = [
  { value: 'minute', label: '每 N 分钟' },
  { value: 'hour', label: '每 N 小时' },
  { value: 'day', label: '每天' },
  { value: 'week', label: '每周' },
  { value: 'month', label: '每月' }
]

export const WEEK_OPTIONS = WEEK_LABELS.map((label, value) => ({ value, label }))

export const MONTH_OPTIONS = MONTH_LABELS.map((label, value) => ({
  value: value + 1,
  label
}))

export const HOUR_OPTIONS = Array.from({ length: 24 }, (_, h) => ({
  value: h,
  label: String(h).padStart(2, '0')
}))

export const MINUTE_OPTIONS = Array.from({ length: 60 }, (_, m) => ({
  value: m,
  label: String(m).padStart(2, '0')
}))

export const DAY_OF_MONTH_OPTIONS = Array.from({ length: 31 }, (_, d) => ({
  value: d + 1,
  label: `${d + 1} 日`
}))

/** UI 中"value"字段（每 N）的可选范围 */
export const FREQUENCY_VALUE_OPTIONS = Array.from({ length: 30 }, (_, i) => ({
  value: i + 1,
  label: `${i + 1}`
}))

export { WEEK_LABELS, MONTH_LABELS }
