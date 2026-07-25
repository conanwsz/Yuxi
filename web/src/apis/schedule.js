import { apiAdminDelete, apiAdminGet, apiAdminPatch, apiAdminPost, apiGet } from './base'

const BASE_URL = '/api/schedules'

const buildQuery = (params = {}) => {
  const query = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') return
    query.set(key, String(value))
  })
  const serialized = query.toString()
  return serialized ? `?${serialized}` : ''
}

export const scheduleApi = {
  fetchSchedules: (params = {}) => apiAdminGet(`${BASE_URL}${buildQuery(params)}`),

  fetchScheduleDetail: (id) => apiAdminGet(`${BASE_URL}/${id}`),

  createSchedule: (payload) => apiAdminPost(BASE_URL, payload),

  updateSchedule: (id, payload) => apiAdminPatch(`${BASE_URL}/${id}`, payload),

  deleteSchedule: (id) => apiAdminDelete(`${BASE_URL}/${id}`),

  fireSchedule: (id) => apiAdminPost(`${BASE_URL}/${id}/fire`, {}),

  fetchExecutions: (id, params = {}) =>
    apiAdminGet(`${BASE_URL}/${id}/executions${buildQuery(params)}`),

  fetchAgentOptions: async () => {
    // 只展示主 agent（过滤 subagent），subagent 不应该被直接调度。
    const response = await apiGet('/api/agent?include_subagents=true')
    const list = response?.agents || []
    return list
      .filter((agent) => !agent.is_subagent)
      .map((agent) => ({
        slug: agent.slug || agent.agent_id || agent.id,
        name: agent.name || agent.slug || agent.id
      }))
  }
}
