<template>
  <div class="schedule-container">
    <!-- 顶部 toolbar -->
    <div class="schedule-toolbar">
      <a-input
        v-model:value="searchKeyword"
        placeholder="搜索名称、智能体或触发规则"
        allow-clear
        class="schedule-search"
        @press-enter="handleSearch"
      >
        <template #prefix>
          <Search :size="14" />
        </template>
      </a-input>
      <a-select
        v-model:value="enabledFilter"
        :options="ENABLED_FILTER_OPTIONS"
        class="schedule-filter"
        @change="handleFilterChange"
      />
      <a-button @click="handleRefresh">
        <template #icon><RefreshCw :size="14" /></template>
        刷新
      </a-button>
      <a-button type="primary" @click="openCreateModal">
        <template #icon><Plus :size="14" /></template>
        新建定时任务
      </a-button>
    </div>

    <!-- 描述 -->
    <p class="schedule-description">
      配置定时触发规则，到点自动创建 AgentRun 投到 ARQ
      队列执行。频率以服务器时间（Asia/Shanghai）为准。
    </p>

    <!-- 主表格 -->
    <a-table
      :data-source="filteredSchedules"
      :columns="columns"
      :loading="store.loading"
      :pagination="pagination"
      :row-key="(record) => record.id"
      size="middle"
      @change="handleTableChange"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'name'">
          <a class="cell-name" @click="openDetailModal(record)">{{ record.name }}</a>
        </template>
        <template v-else-if="column.key === 'agent'">
          <div class="cell-agent">
            <span class="cell-agent-name">{{ record.agent_name || record.agent_slug }}</span>
          </div>
        </template>
        <template v-else-if="column.key === 'cron'">
          <a-tag color="blue" class="cell-cron">{{
            record.cron_label || record.cron_expression
          }}</a-tag>
        </template>
        <template v-else-if="column.key === 'enabled'">
          <a-switch
            :checked="record.enabled"
            :loading="togglingId === record.id"
            checked-children="启用"
            un-checked-children="停用"
            @change="(val) => handleToggleEnabled(record, val)"
          />
        </template>
        <template v-else-if="column.key === 'next_fire_at'">
          <div v-if="record.next_fire_at" class="cell-time">
            <span>{{ formatDateTime(record.next_fire_at) }}</span>
            <span class="cell-time-rel">{{ formatRelative(record.next_fire_at) }}</span>
          </div>
          <span v-else class="cell-empty">—</span>
        </template>
        <template v-else-if="column.key === 'last_fired_at'">
          <div v-if="record.last_fired_at" class="cell-time">
            <span>{{ formatDateTime(record.last_fired_at) }}</span>
            <span class="cell-time-rel">{{ formatRelative(record.last_fired_at) }}</span>
          </div>
          <span v-else class="cell-empty">—</span>
        </template>
        <template v-else-if="column.key === 'actions'">
          <a-space>
            <a-button type="text" size="small" @click="openEditModal(record)">
              <template #icon><Edit3 :size="14" /></template>
              编辑
            </a-button>
            <a-button type="text" size="small" @click="requestFire(record)">
              <template #icon><Flame :size="14" /></template>
              触发
            </a-button>
            <a-button type="text" size="small" danger @click="requestDelete(record)">
              <template #icon><Trash2 :size="14" /></template>
              删除
            </a-button>
          </a-space>
        </template>
      </template>

      <template #emptyText>
        <a-empty description="还没有定时任务，点右上角「新建定时任务」开始" />
      </template>
    </a-table>

    <!-- 新建 / 编辑弹窗 -->
    <a-modal
      v-model:open="formModal.open"
      :title="formModal.mode === 'create' ? '新建定时任务' : '编辑定时任务'"
      :width="640"
      :confirm-loading="formModal.submitting"
      :mask-closable="!formModal.submitting"
      :keyboard="!formModal.submitting"
      ok-text="确认"
      cancel-text="取消"
      :ok-button-props="{ disabled: !formModal.canSubmit }"
      @ok="handleFormSubmit"
      @cancel="closeFormModal"
    >
      <a-form
        ref="formRef"
        :model="formState"
        :rules="formRules"
        layout="vertical"
        :disabled="formModal.submitting"
      >
        <a-row :gutter="16">
          <a-col :span="12">
            <a-form-item label="名称" required>
              <a-input
                v-model:value="formState.name"
                placeholder="给定时任务起个名字"
                :maxlength="50"
                show-count
              />
            </a-form-item>
          </a-col>
          <a-col :span="12">
            <a-form-item label="Agent" required>
              <a-select
                v-model:value="formState.agent_slug"
                placeholder="选择 Agent"
                :options="agentOptions"
                :loading="agentOptionsLoading"
                :filter-option="filterByLabel"
                show-search
              />
            </a-form-item>
          </a-col>
        </a-row>

        <a-form-item label="指令" required>
          <a-textarea
            v-model:value="formState.query"
            placeholder="描述希望 Agent 执行的操作"
            :auto-size="{ minRows: 3, maxRows: 6 }"
            :maxlength="8000"
            show-count
          />
        </a-form-item>

        <a-form-item label="描述（可选）">
          <a-textarea
            v-model:value="formState.description"
            placeholder="写清楚这个定时任务做什么"
            :auto-size="{ minRows: 2, maxRows: 3 }"
            :maxlength="500"
            show-count
          />
        </a-form-item>

        <a-form-item label="触发频率" required>
          <div class="frequency-row">
            <a-select
              class="frequency-type"
              v-model:value="formState.frequency.type"
              :options="FREQUENCY_OPTIONS"
              @change="handleFrequencyTypeChange"
            />
            <template v-if="formState.frequency.type === 'minute'">
              <a-select
                class="frequency-value"
                v-model:value="formState.frequency.value"
                :options="FREQUENCY_VALUE_OPTIONS"
              />
              <span class="form-unit">分钟</span>
            </template>
            <template v-else-if="formState.frequency.type === 'hour'">
              <a-select
                class="frequency-value"
                v-model:value="formState.frequency.value"
                :options="FREQUENCY_VALUE_OPTIONS"
              />
              <span class="form-unit">小时</span>
              <a-select
                class="frequency-minute"
                v-model:value="formState.frequency.minute"
                :options="MINUTE_OPTIONS"
              />
              <span class="form-unit">分</span>
            </template>
            <template v-else-if="formState.frequency.type === 'day'">
              <a-select
                class="frequency-time"
                v-model:value="formState.frequency.hour"
                :options="HOUR_OPTIONS"
              />
              <span class="form-unit">时</span>
              <a-select
                class="frequency-time"
                v-model:value="formState.frequency.minute"
                :options="MINUTE_OPTIONS"
              />
              <span class="form-unit">分</span>
            </template>
            <template v-else-if="formState.frequency.type === 'week'">
              <a-select
                class="frequency-week"
                v-model:value="formState.frequency.dayOfWeek"
                :options="WEEK_OPTIONS"
              />
              <a-select
                class="frequency-time"
                v-model:value="formState.frequency.hour"
                :options="HOUR_OPTIONS"
              />
              <span class="form-unit">时</span>
              <a-select
                class="frequency-time"
                v-model:value="formState.frequency.minute"
                :options="MINUTE_OPTIONS"
              />
              <span class="form-unit">分</span>
            </template>
            <template v-else-if="formState.frequency.type === 'month'">
              <a-select
                class="frequency-dom"
                v-model:value="formState.frequency.dayOfMonth"
                :options="DAY_OF_MONTH_OPTIONS"
              />
              <a-select
                class="frequency-time"
                v-model:value="formState.frequency.hour"
                :options="HOUR_OPTIONS"
              />
              <span class="form-unit">时</span>
              <a-select
                class="frequency-time"
                v-model:value="formState.frequency.minute"
                :options="MINUTE_OPTIONS"
              />
              <span class="form-unit">分</span>
            </template>
          </div>
          <div class="form-preview">
            <span class="form-preview-label">预览：</span>
            <a-tag color="blue">{{ previewCronLabel }}</a-tag>
          </div>
        </a-form-item>

        <a-form-item>
          <a-switch
            v-model:checked="formState.enabled"
            checked-children="启用"
            un-checked-children="停用"
          />
          <span class="form-helper" style="margin-left: 8px">
            停用后不再触发，但保留配置与历史
          </span>
        </a-form-item>
      </a-form>
    </a-modal>

    <!-- 详情弹窗 -->
    <a-modal
      v-model:open="detailModal.open"
      :title="detailModal.schedule?.name || '任务详情'"
      :footer="null"
      :width="640"
    >
      <a-descriptions v-if="detailModal.schedule" :column="1" bordered size="small">
        <a-descriptions-item label="名称">{{ detailModal.schedule.name }}</a-descriptions-item>
        <a-descriptions-item label="描述">{{
          detailModal.schedule.description || '—'
        }}</a-descriptions-item>
        <a-descriptions-item label="智能体">
          <div class="cell-agent">
            <span class="cell-agent-name">{{ detailModal.schedule.agent_name }}</span>
          </div>
        </a-descriptions-item>
        <a-descriptions-item label="指令">
          <pre class="cell-pre">{{ detailModal.schedule.query }}</pre>
        </a-descriptions-item>
        <a-descriptions-item label="触发规则">
          <a-tag color="blue">{{
            detailModal.schedule.cron_label || detailModal.schedule.cron_expression
          }}</a-tag>
        </a-descriptions-item>
        <a-descriptions-item label="下次触发">
          <span v-if="detailModal.schedule.next_fire_at">
            {{ formatDateTime(detailModal.schedule.next_fire_at) }}
            <span class="cell-time-rel"
              >({{ formatRelative(detailModal.schedule.next_fire_at) }})</span
            >
          </span>
          <span v-else>—</span>
        </a-descriptions-item>
        <a-descriptions-item label="最近触发">
          <span v-if="detailModal.schedule.last_fired_at">
            {{ formatDateTime(detailModal.schedule.last_fired_at) }}
            <span class="cell-time-rel"
              >({{ formatRelative(detailModal.schedule.last_fired_at) }})</span
            >
          </span>
          <span v-else>—</span>
        </a-descriptions-item>
        <a-descriptions-item label="创建时间">
          {{ formatDateTime(detailModal.schedule.created_at) }}
        </a-descriptions-item>
      </a-descriptions>
      <div class="detail-actions">
        <a-button @click="openEditFromDetail">编辑</a-button>
        <a-button @click="openExecutionsFromDetail">查看执行历史</a-button>
      </div>
    </a-modal>

    <!-- 执行历史抽屉 -->
    <a-drawer
      v-model:open="executionsDrawer.open"
      :title="`执行历史 · ${executionsDrawer.schedule?.name || ''}`"
      :width="640"
      :footer="null"
    >
      <div v-if="executionsDrawer.loading" class="executions-loading">
        <a-spin />
      </div>
      <a-empty v-else-if="!executionsDrawer.items.length" description="暂无执行记录" />
      <a-list v-else :data-source="executionsDrawer.items" item-layout="vertical">
        <template #renderItem="{ item }">
          <a-list-item :key="item.id">
            <a-list-item-meta>
              <template #title>
                <a-tag :color="statusColor(item.status)">{{ item.status }}</a-tag>
                <span class="exec-time">{{
                  formatDateTime(item.fired_at || item.scheduled_at)
                }}</span>
              </template>
              <template #description>
                <div v-if="item.started_at || item.completed_at" class="exec-detail">
                  开始 {{ formatDateTime(item.started_at) }} · 完成
                  {{ formatDateTime(item.completed_at) }}
                </div>
                <div v-if="item.result_summary" class="exec-summary">{{ item.result_summary }}</div>
                <div v-if="item.error" class="exec-error">错误：{{ item.error }}</div>
              </template>
            </a-list-item-meta>
          </a-list-item>
        </template>
      </a-list>
    </a-drawer>
  </div>
</template>

<script setup>
import { ref, computed, reactive, onMounted, onUnmounted, watch } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { Search, RefreshCw, Plus, Edit3, Flame, Trash2 } from 'lucide-vue-next'
import { useScheduleStore } from '@/stores/schedule'
import { scheduleApi } from '@/apis/schedule'
import { formatDateTime, formatRelative } from '@/utils/time'
import {
  humanizeCron,
  buildCron,
  FREQUENCY_OPTIONS,
  WEEK_OPTIONS,
  HOUR_OPTIONS,
  MINUTE_OPTIONS,
  DAY_OF_MONTH_OPTIONS,
  FREQUENCY_VALUE_OPTIONS
} from '@/utils/cron'

const store = useScheduleStore()

const ENABLED_FILTER_OPTIONS = [
  { value: 'all', label: '全部' },
  { value: 'true', label: '已启用' },
  { value: 'false', label: '已停用' }
]

const searchKeyword = ref('')
const enabledFilter = ref('all')
const agentOptions = ref([])
const agentOptionsLoading = ref(false)
const togglingId = ref(null)

const pagination = reactive({ current: 1, pageSize: 10, total: 0, showSizeChanger: true })
const columns = [
  { title: '名称', dataIndex: 'name', key: 'name', width: 200, ellipsis: true },
  { title: '智能体', dataIndex: 'agent_name', key: 'agent', width: 180, ellipsis: true },
  { title: '触发规则', dataIndex: 'cron_label', key: 'cron', width: 200, ellipsis: true },
  { title: '启用', dataIndex: 'enabled', key: 'enabled', width: 100 },
  { title: '下次触发', dataIndex: 'next_fire_at', key: 'next_fire_at', width: 180 },
  { title: '最近触发', dataIndex: 'last_fired_at', key: 'last_fired_at', width: 180 },
  { title: '操作', key: 'actions', width: 240, fixed: 'right' }
]

const detailModal = reactive({ open: false, schedule: null })
const executionsDrawer = reactive({ open: false, schedule: null, items: [], loading: false })
const formRef = ref(null)

const defaultFrequency = () => ({
  type: 'day',
  value: 1,
  minute: 0,
  hour: 9,
  dayOfWeek: 1,
  dayOfMonth: 1
})

const formModal = reactive({
  open: false,
  mode: 'create', // 'create' | 'edit'
  editingId: null,
  submitting: false,
  canSubmit: false
})

const formState = reactive({
  name: '',
  description: '',
  agent_slug: '',
  query: '',
  enabled: true,
  frequency: defaultFrequency()
})

const formRules = {
  name: [{ required: true, message: '请填写任务名称', trigger: 'blur' }],
  agent_slug: [{ required: true, message: '请选择 Agent', trigger: 'change' }],
  query: [{ required: true, message: '请填写 Agent 指令', trigger: 'blur' }]
}

// 预览 cron 翻译结果
const previewCronLabel = computed(() => {
  try {
    const cron = buildCron(formState.frequency)
    const human = humanizeCron(cron)
    return `${human.label}（${cron}）`
  } catch {
    return '请补全配置'
  }
})

// 提交按钮可用性
watch(
  () => [formState.name, formState.agent_slug, formState.query, formState.frequency.type],
  () => {
    formModal.canSubmit = Boolean(
      formState.name?.trim() &&
      formState.agent_slug &&
      formState.query?.trim() &&
      formState.frequency.type
    )
  },
  { immediate: true }
)

const filteredSchedules = computed(() => {
  let list = store.schedules
  if (enabledFilter.value !== 'all') {
    const want = enabledFilter.value === 'true'
    list = list.filter((s) => Boolean(s.enabled) === want)
  }
  const keyword = searchKeyword.value.trim().toLowerCase()
  if (keyword) {
    list = list.filter(
      (s) =>
        (s.name || '').toLowerCase().includes(keyword) ||
        (s.agent_name || '').toLowerCase().includes(keyword) ||
        (s.cron_label || '').toLowerCase().includes(keyword)
    )
  }
  return list
})

function statusColor(status) {
  switch (status) {
    case 'success':
      return 'green'
    case 'failed':
      return 'red'
    case 'cancelled':
      return 'orange'
    case 'running':
      return 'blue'
    case 'pending':
      return 'default'
    case 'skipped':
      return 'default'
    default:
      return 'default'
  }
}

function filterByLabel(input, option) {
  return (option?.label ?? '').toLowerCase().includes(input.toLowerCase())
}

async function loadAgentOptions() {
  agentOptionsLoading.value = true
  try {
    const response = await scheduleApi.fetchAgentOptions()
    const list = response?.agents || response?.items || response || []
    agentOptions.value = list
      .filter((a) => a && (a.slug || a.agent_slug))
      .map((a) => ({
        value: a.slug || a.agent_slug,
        label: a.display_name || a.name || a.slug || a.agent_slug
      }))
  } catch (error) {
    console.warn('加载 Agent 列表失败', error)
  } finally {
    agentOptionsLoading.value = false
  }
}

function handleRefresh() {
  store.loadSchedules()
}

function handleFilterChange() {
  pagination.current = 1
}

function handleSearch() {
  pagination.current = 1
}

function handleTableChange(pager) {
  pagination.current = pager.current
  pagination.pageSize = pager.pageSize
}

function handleFrequencyTypeChange() {
  // 切换频率类型时给个合理默认
  const f = formState.frequency
  if (f.type === 'day' && f.hour == null) f.hour = 9
  if (f.type === 'week' && f.dayOfWeek == null) f.dayOfWeek = 1
  if (f.type === 'month' && f.dayOfMonth == null) f.dayOfMonth = 1
  if (f.type === 'hour' && f.value == null) f.value = 1
  if (f.type === 'minute' && f.value == null) f.value = 30
}

function openCreateModal() {
  formModal.mode = 'create'
  formModal.editingId = null
  Object.assign(formState, {
    name: '',
    description: '',
    agent_slug: '',
    query: '',
    enabled: true,
    frequency: defaultFrequency()
  })
  formModal.open = true
}

function openEditModal(schedule) {
  formModal.mode = 'edit'
  formModal.editingId = schedule.id
  const humanized = humanizeCron(schedule.cron_expression || '')
  Object.assign(formState, {
    name: schedule.name || '',
    description: schedule.description || '',
    agent_slug: schedule.agent_slug || '',
    query: schedule.query || '',
    enabled: Boolean(schedule.enabled),
    frequency: {
      type: humanized.type,
      value: humanized.value,
      minute: humanized.minute,
      hour: humanized.hour,
      dayOfWeek: humanized.dayOfWeek,
      dayOfMonth: humanized.dayOfMonth
    }
  })
  formModal.open = true
}

function closeFormModal() {
  if (formModal.submitting) return
  formModal.open = false
}

async function handleFormSubmit() {
  try {
    await formRef.value?.validate()
  } catch {
    return
  }
  const cron = buildCron(formState.frequency)
  const payload = {
    name: formState.name.trim(),
    description: formState.description?.trim() || null,
    agent_slug: formState.agent_slug,
    query: formState.query,
    cron_expression: cron,
    enabled: formState.enabled
  }
  formModal.submitting = true
  try {
    if (formModal.mode === 'create') {
      await store.create(payload)
    } else {
      await store.update(formModal.editingId, payload)
    }
    formModal.open = false
  } catch (error) {
    message.error(error?.message || '保存失败')
  } finally {
    formModal.submitting = false
  }
}

function openDetailModal(schedule) {
  detailModal.schedule = schedule
  detailModal.open = true
}

function openEditFromDetail() {
  if (!detailModal.schedule) return
  const s = detailModal.schedule
  detailModal.open = false
  openEditModal(s)
}

async function openExecutionsFromDetail() {
  if (!detailModal.schedule) return
  const id = detailModal.schedule.id
  detailModal.open = false
  await openExecutions(id)
}

async function openExecutions(id) {
  executionsDrawer.schedule = store.schedules.find((s) => s.id === id) || detailModal.schedule
  executionsDrawer.items = []
  executionsDrawer.loading = true
  executionsDrawer.open = true
  try {
    const response = await scheduleApi.fetchExecutions(id, { limit: 50 })
    executionsDrawer.items = (response?.executions || []).map((e) => ({
      id: e.id,
      schedule_id: e.schedule_id,
      agent_run_id: e.agent_run_id,
      scheduled_at: e.scheduled_at,
      fired_at: e.fired_at,
      started_at: e.started_at,
      completed_at: e.completed_at,
      status: e.status || 'pending',
      result_summary: e.result_summary || '',
      error: e.error || ''
    }))
  } catch (error) {
    message.error(error?.message || '加载执行历史失败')
  } finally {
    executionsDrawer.loading = false
  }
}

async function handleToggleEnabled(schedule, val) {
  togglingId.value = schedule.id
  try {
    await store.update(schedule.id, { enabled: val })
  } finally {
    togglingId.value = null
  }
}

function requestFire(schedule) {
  Modal.confirm({
    title: '立即触发',
    content: `将立即执行「${schedule.name}」，不等下次 cron。`,
    okText: '确认',
    cancelText: '取消',
    onOk: async () => {
      try {
        await scheduleApi.fireSchedule(schedule.id)
        message.success('已触发执行')
        await store.refreshSchedule(schedule.id)
      } catch (error) {
        message.error(error?.message || '触发失败')
      }
    }
  })
}

function requestDelete(schedule) {
  Modal.confirm({
    title: '删除定时任务',
    content: `确认删除「${schedule.name}」？其执行历史也会一并删除。`,
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    onOk: async () => {
      await store.remove(schedule.id)
    }
  })
}

onMounted(async () => {
  await Promise.all([store.loadSchedules(), loadAgentOptions()])
})

onUnmounted(() => {
  store.reset()
})
</script>

<style scoped>
.schedule-container {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px 0;
}

.schedule-toolbar {
  display: flex;
  gap: 12px;
  align-items: center;
  flex-wrap: wrap;
}

.schedule-search {
  width: 280px;
}

.schedule-filter {
  width: 120px;
}

.schedule-description {
  color: var(--gray-7, #5b6168);
  font-size: 13px;
  margin: 0 0 4px;
}

.cell-name {
  color: var(--main-color, #1d6ce8);
  cursor: pointer;
  font-weight: 500;
}

.cell-agent {
  display: flex;
  flex-direction: column;
  line-height: 1.4;
}

.cell-cron {
  font-family: 'SF Mono', Menlo, Consolas, monospace;
  font-size: 12px;
}

.cell-time {
  display: flex;
  flex-direction: column;
  line-height: 1.4;
}

.cell-time-rel {
  font-size: 12px;
  color: var(--gray-7, #5b6168);
}

.cell-empty {
  color: var(--gray-6, #8a8f98);
}

.cell-pre {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: inherit;
  font-size: 13px;
  color: var(--gray-10, #1f2329);
}

.form-helper {
  color: var(--gray-7, #5b6168);
  font-size: 13px;
  padding-left: 4px;
}

.frequency-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.frequency-type {
  width: 130px;
}
.frequency-value {
  width: 100px;
}
.frequency-time {
  width: 100px;
}
.frequency-week {
  width: 110px;
}
.frequency-dom {
  width: 110px;
}
.frequency-minute {
  width: 100px;
}

.form-unit {
  color: var(--gray-7, #5b6168);
  font-size: 13px;
  white-space: nowrap;
}

.form-preview {
  margin-top: 8px;
  padding: 8px 12px;
  background: var(--gray-2, #f7f8fa);
  border-radius: 4px;
  font-size: 13px;
  color: var(--gray-10, #1f2329);
}

.form-preview-label {
  color: var(--gray-7, #5b6168);
  margin-right: 8px;
}

.detail-actions {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
  margin-top: 16px;
}

.executions-loading {
  padding: 40px 0;
  display: flex;
  justify-content: center;
}

.exec-time {
  margin-left: 8px;
  font-size: 12px;
  color: var(--gray-7, #5b6168);
}

.exec-detail {
  font-size: 12px;
  color: var(--gray-7, #5b6168);
  margin-top: 2px;
}

.exec-summary {
  font-size: 13px;
  margin-top: 6px;
  color: var(--gray-10, #1f2329);
  white-space: pre-wrap;
}

.exec-error {
  font-size: 12px;
  margin-top: 6px;
  color: var(--color-error, #d9363e);
  white-space: pre-wrap;
}
</style>
