import assert from 'node:assert/strict'
import test from 'node:test'

import {
  formatElapsedDuration,
  getToolGroupDurationMs,
  parseTimestampMs
} from '../../src/utils/toolCallDuration.js'

test('parseTimestampMs 能解析 ISO 时间与毫秒时间戳', () => {
  assert.equal(parseTimestampMs(null), null)
  assert.equal(parseTimestampMs(''), null)
  assert.equal(parseTimestampMs('not-a-date'), null)
  assert.equal(parseTimestampMs(1_700_000_000_000), 1_700_000_000_000)
  assert.equal(parseTimestampMs('2026-09-04T02:00:00.000Z'), Date.parse('2026-09-04T02:00:00.000Z'))
})

test('formatElapsedDuration 按运行中/完成态格式化耗时', () => {
  assert.equal(formatElapsedDuration(-1), '')
  assert.equal(formatElapsedDuration(Number.NaN), '')
  assert.equal(formatElapsedDuration(4200, { running: true }), '4.2s')
  assert.equal(formatElapsedDuration(12000), '12s')
  assert.equal(formatElapsedDuration(12500), '12.5s')
  assert.equal(formatElapsedDuration(83000), '1m 23s')
  assert.equal(formatElapsedDuration(60000), '1m 00s')
  assert.equal(formatElapsedDuration(119500), '2m 00s')
})

test('getToolGroupDurationMs 用全部工具的起止时间计算墙钟耗时', () => {
  const duration = getToolGroupDurationMs([
    {
      started_at: '2026-09-04T02:00:00.000Z',
      completed_at: '2026-09-04T02:00:01.200Z'
    },
    {
      started_at: '2026-09-04T02:00:00.100Z',
      completed_at: '2026-09-04T02:00:02.400Z'
    }
  ])

  assert.equal(duration, 2400)
})

test('getToolGroupDurationMs 在缺少落库时间时回退到前端实时起点', () => {
  assert.equal(
    getToolGroupDurationMs([{ id: 'call-1' }], { now: 1_500, liveStartedAt: 1_000 }),
    500
  )
  assert.equal(getToolGroupDurationMs([{ id: 'call-1' }]), null)
})

test('getToolGroupDurationMs 完整起止时间优先于实时起点', () => {
  const duration = getToolGroupDurationMs(
    [
      {
        started_at: '2026-09-04T02:00:00.000Z',
        completed_at: '2026-09-04T02:00:02.000Z'
      }
    ],
    {
      now: Date.parse('2026-09-04T02:00:10.000Z'),
      liveStartedAt: Date.parse('2026-09-04T02:00:09.000Z')
    }
  )
  assert.equal(duration, 2000)
})

test('getToolGroupDurationMs 不完整的历史时间不展示耗时', () => {
  assert.equal(
    getToolGroupDurationMs([
      {
        started_at: '2026-09-04T02:00:00.000Z',
        completed_at: '2026-09-04T02:00:02.000Z'
      },
      {
        started_at: '2026-09-04T02:00:00.100Z'
      }
    ]),
    null
  )
})
