import { defineStore } from 'pinia'
import { ref } from 'vue'
import { message } from 'ant-design-vue'
import { scheduleApi } from '@/apis/schedule'
import { useUserStore } from '@/stores/user'
import { humanizeCron } from '@/utils/cron'

const PERMISSION = 'system.schedules.manage'

const toSchedule = (raw = {}) => {
  const humanized = humanizeCron(raw.cron_expression || '')
  return {
    id: raw.id,
    name: raw.name || '未命名计划',
    description: raw.description || '',
    agent_slug: raw.agent_slug || '',
    agent_name: raw.agent_name || raw.agent_slug || '',
    query: raw.query || '',
    cron_expression: raw.cron_expression || '',
    cron_label: humanized.label,
    enabled: raw.enabled ?? true,
    owner_uid: raw.owner_uid || '',
    last_fired_at: raw.last_fired_at,
    next_fire_at: raw.next_fire_at,
    created_at: raw.created_at,
    updated_at: raw.updated_at
  }
}

const toExecution = (raw = {}) => ({
  id: raw.id,
  schedule_id: raw.schedule_id,
  agent_run_id: raw.agent_run_id,
  scheduled_at: raw.scheduled_at,
  fired_at: raw.fired_at,
  started_at: raw.started_at,
  completed_at: raw.completed_at,
  status: raw.status || 'pending',
  result_summary: raw.result_summary || '',
  error: raw.error || ''
})

export const useScheduleStore = defineStore('schedule', () => {
  const userStore = useUserStore()

  const schedules = ref([])
  const loading = ref(false)
  const lastError = ref(null)
  const summary = ref({ total: 0, enabled: 0, disabled: 0 })

  const detailDrawer = ref({ open: false, schedule: null })
  const editDrawer = ref({ open: false, schedule: null })
  const executionsDrawer = ref({ open: false, schedule: null, items: [], loading: false })
  const fireConfirming = ref(false)

  const ensurePermission = () => {
    if (!userStore.hasPermission(PERMISSION)) return false
    return true
  }

  async function loadSchedules(params = {}) {
    if (!ensurePermission()) {
      schedules.value = []
      summary.value = { total: 0, enabled: 0, disabled: 0 }
      return
    }
    loading.value = true
    lastError.value = null
    try {
      const response = await scheduleApi.fetchSchedules(params)
      const list = response?.schedules || []
      schedules.value = list.map(toSchedule)
      const enabled = schedules.value.filter((item) => item.enabled).length
      summary.value = {
        total: schedules.value.length,
        enabled,
        disabled: schedules.value.length - enabled
      }
    } catch (error) {
      console.error('加载定时任务列表失败', error)
      lastError.value = error
    } finally {
      loading.value = false
    }
  }

  async function refreshSchedule(id) {
    if (!id || !ensurePermission()) return
    try {
      const response = await scheduleApi.fetchScheduleDetail(id)
      if (response?.schedule) {
        const next = toSchedule(response.schedule)
        const index = schedules.value.findIndex((item) => item.id === id)
        if (index >= 0) {
          schedules.value.splice(index, 1, { ...schedules.value[index], ...next })
        } else {
          schedules.value.push(next)
        }
      }
    } catch (error) {
      console.error(`刷新定时任务 ${id} 失败`, error)
      lastError.value = error
    }
  }

  async function openDetail(id) {
    if (!id) return
    await refreshSchedule(id)
    const schedule = schedules.value.find((item) => item.id === id) || null
    detailDrawer.value = { open: true, schedule }
  }

  function closeDetail() {
    detailDrawer.value = { open: false, schedule: null }
  }

  function openEditor(schedule = null) {
    editDrawer.value = { open: true, schedule: schedule ? { ...schedule } : null }
  }

  function closeEditor() {
    editDrawer.value = { open: false, schedule: null }
  }

  async function openExecutions(id) {
    if (!id) return
    executionsDrawer.value = {
      open: true,
      schedule: schedules.value.find((item) => item.id === id) || null,
      items: [],
      loading: true
    }
    try {
      const response = await scheduleApi.fetchExecutions(id, { limit: 50 })
      executionsDrawer.value = {
        open: true,
        schedule: schedules.value.find((item) => item.id === id) || null,
        items: (response?.executions || []).map(toExecution),
        loading: false
      }
    } catch (error) {
      console.error(`加载定时任务执行历史 ${id} 失败`, error)
      message.error(error?.message || '加载执行历史失败')
      executionsDrawer.value.loading = false
    }
  }

  function closeExecutions() {
    executionsDrawer.value = { open: false, schedule: null, items: [], loading: false }
  }

  async function create(payload) {
    if (!ensurePermission()) return null
    try {
      const response = await scheduleApi.createSchedule(payload)
      message.success('定时任务已创建')
      await loadSchedules()
      return response?.schedule || null
    } catch (error) {
      console.error('创建定时任务失败', error)
      message.error(error?.message || '创建定时任务失败')
      throw error
    }
  }

  async function update(id, payload) {
    if (!id || !ensurePermission()) return null
    try {
      const response = await scheduleApi.updateSchedule(id, payload)
      message.success('定时任务已更新')
      await refreshSchedule(id)
      return response?.schedule || null
    } catch (error) {
      console.error(`更新定时任务 ${id} 失败`, error)
      message.error(error?.message || '更新定时任务失败')
      throw error
    }
  }

  async function remove(id) {
    if (!id || !ensurePermission()) return false
    try {
      await scheduleApi.deleteSchedule(id)
      message.success('定时任务已删除')
      const index = schedules.value.findIndex((item) => item.id === id)
      if (index >= 0) {
        schedules.value.splice(index, 1)
      }
      const enabled = schedules.value.filter((item) => item.enabled).length
      summary.value = {
        total: schedules.value.length,
        enabled,
        disabled: schedules.value.length - enabled
      }
      return true
    } catch (error) {
      console.error(`删除定时任务 ${id} 失败`, error)
      message.error(error?.message || '删除定时任务失败')
      return false
    }
  }

  function requestFire(id) {
    if (!id) return
    fireConfirming.value = id
  }

  function cancelFire() {
    fireConfirming.value = false
  }

  async function fire(id) {
    if (!id || !ensurePermission()) return
    try {
      await scheduleApi.fireSchedule(id)
      message.success('已触发执行')
      fireConfirming.value = false
      await refreshSchedule(id)
    } catch (error) {
      console.error(`触发定时任务 ${id} 失败`, error)
      message.error(error?.message || '触发定时任务失败')
    }
  }

  function reset() {
    schedules.value = []
    lastError.value = null
    detailDrawer.value = { open: false, schedule: null }
    editDrawer.value = { open: false, schedule: null }
    executionsDrawer.value = { open: false, schedule: null, items: [], loading: false }
    fireConfirming.value = false
    summary.value = { total: 0, enabled: 0, disabled: 0 }
  }

  return {
    schedules,
    loading,
    lastError,
    summary,
    detailDrawer,
    editDrawer,
    executionsDrawer,
    fireConfirming,
    loadSchedules,
    refreshSchedule,
    openDetail,
    closeDetail,
    openEditor,
    closeEditor,
    openExecutions,
    closeExecutions,
    create,
    update,
    remove,
    requestFire,
    cancelFire,
    fire,
    reset
  }
})
