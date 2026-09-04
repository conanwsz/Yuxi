const padSeconds = (value) => String(value).padStart(2, '0')

export const parseTimestampMs = (value) => {
  if (value == null || value === '') return null
  if (typeof value === 'number' && Number.isFinite(value)) return value

  const parsed = Date.parse(String(value))
  return Number.isNaN(parsed) ? null : parsed
}

export const formatElapsedDuration = (ms, { running = false } = {}) => {
  if (!Number.isFinite(ms) || ms < 0) return ''

  const totalSeconds = ms / 1000
  if (running) {
    const elapsed = Math.floor(totalSeconds)
    if (elapsed < 60) return `${elapsed}s`

    const minutes = Math.floor(elapsed / 60)
    const seconds = elapsed % 60
    return `${minutes}m ${padSeconds(seconds)}s`
  }

  if (totalSeconds < 60) {
    const rounded = Math.round(totalSeconds * 10) / 10
    return Number.isInteger(rounded) ? `${Math.round(rounded)}s` : `${rounded}s`
  }

  const roundedSeconds = Math.round(totalSeconds)
  const minutes = Math.floor(roundedSeconds / 60)
  const seconds = roundedSeconds % 60
  return `${minutes}m ${padSeconds(seconds)}s`
}

export const getToolGroupDurationMs = (
  toolCalls,
  { now = Date.now(), liveStartedAt = null } = {}
) => {
  const calls = Array.isArray(toolCalls) ? toolCalls : []
  const starts = []
  const ends = []

  for (const toolCall of calls) {
    const started = parseTimestampMs(toolCall?.started_at)
    const completed = parseTimestampMs(toolCall?.completed_at)
    if (started != null) starts.push(started)
    if (completed != null) ends.push(completed)
  }

  if (calls.length > 0 && starts.length === calls.length && ends.length === calls.length) {
    return Math.max(0, Math.max(...ends) - Math.min(...starts))
  }

  if (liveStartedAt != null) {
    return Math.max(0, now - liveStartedAt)
  }

  return null
}
