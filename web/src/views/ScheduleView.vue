<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { message, Modal } from 'ant-design-vue'
import {
  CalendarClock,
  Edit3,
  Flame,
  History,
  Info,
  Plus,
  RefreshCw,
  Search,
  Trash2
} from 'lucide-vue-next'

import { scheduleApi } from '@/apis/schedule'
import { useScheduleStore } from '@/stores/schedule'
import { useUserStore } from '@/stores/user'
import { formatDateTime, formatRelative } from '@/utils/time'

const scheduleStore = useScheduleStore()
const userStore = useUserStore()

const PERMISSION = 'system.schedules.manage'
const DEFAULT_TZ = 'Asia/Shanghai'

const COMMON_TIMEZONES = [
  'UTC',
  'Asia/Shanghai',
  'Asia/Tokyo',
  'Asia/Singapore',
  'Asia/Hong_Kong',
  'Asia/Taipei',
  'Asia/Seoul',
  'Asia/Kolkata',
  'Europe/London',
  'Europe/Berlin',
  'Europe/Paris',
  'Europe/Moscow',
  'America/New_York',
  'America/Chicago',
  'America/Denver',
  'America/Los_Angeles',
  'Australia/Sydney'
]

const searchKeyword = ref('')
const enabledFilter = ref('all')
const agentOptions = ref([])
const agentOptionsLoading = ref(false)
const firing = ref(false)
const submitting = ref(false)
const detailTab = ref('overview')

const emptyForm = () => ({
  name: '',
  description: '',
  agent_slug: '',
  query: '',
  cron_expression: '0 */1 * * *',
  timezone: DEFAULT_TZ,
  enabled: true
})

const formState = reactive(emptyForm())
const formErrors = ref({})
const editingId = ref(null)

const agentOptionsForSelect = computed(() => {
  const list = agentOptions.value || []
  const sorted = [...list].sort((a, b) => {
    if (a.is_subagent !== b.is_subagent) return a.is_subagent ? 1 : -1
    return String(a.name).localeCompare(String(b.name), 'zh-CN')
  })
  return sorted.map((agent) => ({
    label: agent.is_subagent ? `${agent.name}（子智能体）` : agent.name,
    value: agent.slug
  }))
})

const timezoneOptions = computed(() =>
  COMMON_TIMEZONES.map((tz) => ({ label: tz, value: tz }))
)

const filteredSchedules = computed(() => {
  const keyword = searchKeyword.value.trim().toLowerCase()
  const list = scheduleStore.schedules || []
  return list.filter((item) => {
    if (enabledFilter.value === 'enabled' && !item.enabled) return false
    if (enabledFilter.value === 'disabled' && item.enabled) return false
    if (!keyword) return true
    return (
      String(item.name || '').toLowerCase().includes(keyword) ||
      String(item.agent_name || item.agent_slug || '').toLowerCase().includes(keyword) ||
      String(item.cron_expression || '').toLowerCase().includes(keyword)
    )
  })
})

const columns = [
  { title: '名称', dataIndex: 'name', key: 'name', width: 200, ellipsis: true },
  { title: '智能体', dataIndex: 'agent_name', key: 'agent', width: 180, ellipsis: true },
  {
    title: 'Cron',
    dataIndex: 'cron_expression',
    key: 'cron',
    width: 150,
    ellipsis: true
  },
  { title: '时区', dataIndex: 'timezone', key: 'timezone', width: 140, ellipsis: true },
  {
    title: '启用',
    dataIndex: 'enabled',
    key: 'enabled',
    width: 90,
    align: 'center'
  },
  {
    title: '下次触发',
    dataIndex: 'next_fire_at',
    key: 'next_fire_at',
    width: 170
  },
  {
    title: '最近触发',
    dataIndex: 'last_fired_at',
    key: 'last_fired_at',
    width: 170
  },
  { title: '操作', key: 'actions', width: 230, align: 'right', fixed: 'right' }
]

const enabledFilterOptions = [
  { label: '全部', value: 'all' },
  { label: '已启用', value: 'enabled' },
  { label: '已停用', value: 'disabled' }
]

const statusLabels = {
  pending: '待触发',
  running: '执行中',
  success: '成功',
  failed: '失败',
  skipped: '已跳过'
}

const statusColorMap = {
  pending: 'default',
  running: 'processing',
  success: 'success',
  failed: 'error',
  skipped: 'warning'
}

const hasPermission = computed(() => userStore.hasPermission(PERMISSION))

const loadAgentOptions = async () => {
  if (agentOptions.value.length || agentOptionsLoading.value) return
  agentOptionsLoading.value = true
  try {
    agentOptions.value = await scheduleApi.fetchAgentOptions()
  } catch (error) {
    console.error('加载智能体选项失败', error)
    message.error(error?.message || '加载智能体选项失败')
  } finally {
    agentOptionsLoading.value = false
  }
}

const loadAll = async () => {
  if (!hasPermission.value) return
  await Promise.all([scheduleStore.loadSchedules(), loadAgentOptions()])
}

const openCreate = () => {
  editingId.value = null
  formErrors.value = {}
  Object.assign(formState, emptyForm())
  scheduleStore.openEditor(null)
}

const openEdit = (record) => {
  editingId.value = record.id
  formErrors.value = {}
  Object.assign(formState, {
    name: record.name || '',
    description: record.description || '',
    agent_slug: record.agent_slug || '',
    query: record.query || '',
    cron_expression: record.cron_expression || '',
    timezone: record.timezone || DEFAULT_TZ,
    enabled: record.enabled ?? true
  })
  scheduleStore.openEditor(record)
}

const validateForm = () => {
  const errors = {}
  if (!formState.name.trim()) errors.name = '请填写名称'
  if (!formState.agent_slug) errors.agent_slug = '请选择智能体'
  if (!formState.query.trim()) errors.query = '请填写 query'
  if (!formState.cron_expression.trim()) errors.cron_expression = '请填写 cron 表达式'
  if (!formState.timezone) errors.timezone = '请选择时区'
  formErrors.value = errors
  return Object.keys(errors).length === 0
}

const buildPayload = () => ({
  name: formState.name.trim(),
  description: formState.description.trim() || null,
  agent_slug: formState.agent_slug,
  query: formState.query,
  cron_expression: formState.cron_expression.trim(),
  timezone: formState.timezone,
  enabled: formState.enabled
})

const submitForm = async () => {
  if (!validateForm()) return
  submitting.value = true
  try {
    if (editingId.value) {
      await scheduleStore.update(editingId.value, buildPayload())
    } else {
      await scheduleStore.create(buildPayload())
    }
    scheduleStore.closeEditor()
  } catch {
    // 错误已经在 store 里提示过；保留在抽屉里便于修正
  } finally {
    submitting.value = false
  }
}

const closeEdit = () => {
  scheduleStore.closeEditor()
  editingId.value = null
  formErrors.value = {}
}

const openRowDetail = async (record) => {
  detailTab.value = 'overview'
  await scheduleStore.openDetail(record.id)
}

const closeRowDetail = () => {
  scheduleStore.closeDetail()
  detailTab.value = 'overview'
}

const handleFire = (record) => {
  if (firing.value) return
  Modal.confirm({
    title: `立即触发「${record.name}」?`,
    content: '将立即写一条 execution 入队执行，不等下次 cron。',
    okText: '立即触发',
    okType: 'primary',
    cancelText: '取消',
    onOk: async () => {
      firing.value = true
      try {
        await scheduleApi.fireSchedule(record.id)
        message.success('已触发执行')
        await scheduleStore.refreshSchedule(record.id)
      } catch (error) {
        message.error(error?.message || '触发失败')
      } finally {
        firing.value = false
      }
    }
  })
}

const handleDelete = (record) => {
  Modal.confirm({
    title: `删除「${record.name}」?`,
    content: '删除会同时清除该计划的所有执行历史，且不可恢复。',
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    onOk: async () => {
      await scheduleStore.remove(record.id)
    }
  })
}

const editingRecord = computed(() => scheduleStore.editDrawer.schedule)
const detailRecord = computed(() => scheduleStore.detailDrawer.schedule)

watch(editingRecord, (next) => {
  if (next && next.id !== editingId.value) {
    // 抽屉被外部触发为编辑模式时同步表单（如「在详情里点编辑」）
    openEdit(next)
  }
})

onMounted(() => {
  if (hasPermission.value) {
    loadAll()
  }
})
</script>

<template>
  <div class="schedule-view">
    <div class="schedule-header">
      <div class="header-text">
        <h2 class="page-title">定时任务</h2>
        <p class="page-subtitle">
          按 cron 表达式定时触发智能体，到点自动创建 AgentRun 投递到现有 ARQ 队列执行。
        </p>
      </div>
      <div class="header-stats">
        <span class="stat-pill">共 {{ scheduleStore.summary.total }} 条</span>
        <span class="stat-pill stat-enabled">启用 {{ scheduleStore.summary.enabled }}</span>
        <span class="stat-pill stat-disabled">停用 {{ scheduleStore.summary.disabled }}</span>
      </div>
    </div>

    <div v-if="!hasPermission" class="permission-empty">
      <a-empty description="当前账号没有 system.schedules.manage 权限" />
    </div>

    <template v-else>
      <div class="schedule-toolbar">
        <a-input
          v-model:value="searchKeyword"
          allow-clear
          placeholder="搜索名称、智能体或 cron"
          class="schedule-search"
        >
          <template #prefix><Search :size="14" /></template>
        </a-input>
        <a-select
          v-model:value="enabledFilter"
          :options="enabledFilterOptions"
          class="schedule-filter"
        />
        <a-button class="lucide-icon-btn" :loading="scheduleStore.loading" @click="loadAll">
          <template #icon><RefreshCw :size="14" /></template>
          刷新
        </a-button>
        <a-button
          v-if="userStore.hasPermission(PERMISSION)"
          type="primary"
          class="lucide-icon-btn"
          @click="openCreate"
        >
          <template #icon><Plus :size="14" /></template>
          新建 schedule
        </a-button>
      </div>

      <a-alert
        v-if="scheduleStore.lastError"
        type="error"
        show-icon
        class="schedule-alert"
        :message="scheduleStore.lastError.message || '加载失败'"
      />

      <a-spin :spinning="scheduleStore.loading">
        <a-table
          v-if="filteredSchedules.length"
          class="schedule-table"
          :columns="columns"
          :data-source="filteredSchedules"
          :row-key="(record) => record.id"
          :pagination="{ pageSize: 10, showSizeChanger: false, hideOnSinglePage: true }"
          :scroll="{ x: 1080 }"
          size="middle"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'name'">
              <div class="cell-name">
                <CalendarClock :size="16" class="cell-name-icon" />
                <div class="cell-name-text">
                  <span class="cell-name-title">{{ record.name }}</span>
                  <span v-if="record.description" class="cell-name-desc">
                    {{ record.description }}
                  </span>
                </div>
              </div>
            </template>
            <template v-else-if="column.key === 'agent'">
              <div class="cell-agent">
                <span class="cell-agent-name">{{ record.agent_name || record.agent_slug }}</span>
                <span v-if="record.agent_name && record.agent_slug" class="cell-agent-slug">
                  {{ record.agent_slug }}
                </span>
              </div>
            </template>
            <template v-else-if="column.key === 'cron'">
              <code class="cell-cron">{{ record.cron_expression }}</code>
            </template>
            <template v-else-if="column.key === 'enabled'">
              <a-tag :color="record.enabled ? 'green' : 'default'">
                {{ record.enabled ? '已启用' : '已停用' }}
              </a-tag>
            </template>
            <template v-else-if="column.key === 'next_fire_at'">
              <div class="cell-time">
                <span>{{ formatDateTime(record.next_fire_at) }}</span>
                <span class="cell-time-rel">{{ formatRelative(record.next_fire_at) }}</span>
              </div>
            </template>
            <template v-else-if="column.key === 'last_fired_at'">
              <div class="cell-time">
                <span>{{ formatDateTime(record.last_fired_at) }}</span>
                <span class="cell-time-rel">{{ formatRelative(record.last_fired_at) }}</span>
              </div>
            </template>
            <template v-else-if="column.key === 'actions'">
              <a-space :size="4" class="row-actions">
                <a-tooltip title="查看详情">
                  <a-button
                    type="text"
                    size="small"
                    class="lucide-icon-btn"
                    @click="openRowDetail(record)"
                  >
                    <template #icon><Info :size="14" /></template>
                  </a-button>
                </a-tooltip>
                <a-tooltip title="编辑">
                  <a-button
                    type="text"
                    size="small"
                    class="lucide-icon-btn"
                    @click="openEdit(record)"
                  >
                    <template #icon><Edit3 :size="14" /></template>
                  </a-button>
                </a-tooltip>
                <a-tooltip title="立即触发">
                  <a-button
                    type="text"
                    size="small"
                    class="lucide-icon-btn"
                    :loading="firing"
                    @click="handleFire(record)"
                  >
                    <template #icon><Flame :size="14" /></template>
                  </a-button>
                </a-tooltip>
                <a-tooltip title="执行历史">
                  <a-button
                    type="text"
                    size="small"
                    class="lucide-icon-btn"
                    @click="scheduleStore.openExecutions(record.id)"
                  >
                    <template #icon><History :size="14" /></template>
                  </a-button>
                </a-tooltip>
                <a-tooltip title="删除">
                  <a-button
                    type="text"
                    size="small"
                    danger
                    class="lucide-icon-btn"
                    @click="handleDelete(record)"
                  >
                    <template #icon><Trash2 :size="14" /></template>
                  </a-button>
                </a-tooltip>
              </a-space>
            </template>
          </template>
        </a-table>
        <a-empty
          v-else
          :description="searchKeyword ? '没有匹配的 schedule' : '还没有定时任务，点击「新建 schedule」开始'"
          class="schedule-empty"
        />
      </a-spin>
    </template>

    <!-- 详情抽屉 -->
    <a-drawer
      :open="scheduleStore.detailDrawer.open"
      :width="560"
      title="schedule 详情"
      :destroy-on-close="false"
      @close="closeRowDetail"
    >
      <template v-if="detailRecord" #extra>
        <a-space :size="4">
          <a-button
            size="small"
            class="lucide-icon-btn"
            @click="scheduleStore.openExecutions(detailRecord.id)"
          >
            <template #icon><History :size="14" /></template>
            执行历史
          </a-button>
          <a-button
            size="small"
            type="primary"
            class="lucide-icon-btn"
            @click="openEdit(detailRecord)"
          >
            <template #icon><Edit3 :size="14" /></template>
            编辑
          </a-button>
        </a-space>
      </template>

      <div v-if="!detailRecord" class="detail-loading">
        <a-spin />
      </div>
      <div v-else class="detail-content">
        <a-tabs v-model:active-key="detailTab" class="detail-tabs">
          <a-tab-pane key="overview" tab="概览">
            <div class="detail-section">
              <h3 class="detail-section-title">{{ detailRecord.name }}</h3>
              <p v-if="detailRecord.description" class="detail-section-desc">
                {{ detailRecord.description }}
              </p>
            </div>
            <dl class="detail-grid">
              <div>
                <dt>智能体</dt>
                <dd>{{ detailRecord.agent_name || detailRecord.agent_slug || '-' }}</dd>
              </div>
              <div>
                <dt>状态</dt>
                <dd>
                  <a-tag :color="detailRecord.enabled ? 'green' : 'default'">
                    {{ detailRecord.enabled ? '已启用' : '已停用' }}
                  </a-tag>
                </dd>
              </div>
              <div>
                <dt>Cron 表达式</dt>
                <dd><code>{{ detailRecord.cron_expression }}</code></dd>
              </div>
              <div>
                <dt>时区</dt>
                <dd>{{ detailRecord.timezone }}</dd>
              </div>
              <div>
                <dt>下次触发</dt>
                <dd>{{ formatDateTime(detailRecord.next_fire_at) }}</dd>
              </div>
              <div>
                <dt>最近触发</dt>
                <dd>{{ formatDateTime(detailRecord.last_fired_at) }}</dd>
              </div>
              <div>
                <dt>创建时间</dt>
                <dd>{{ formatDateTime(detailRecord.created_at) }}</dd>
              </div>
              <div>
                <dt>更新时间</dt>
                <dd>{{ formatDateTime(detailRecord.updated_at) }}</dd>
              </div>
            </dl>
            <div class="detail-section">
              <h4 class="detail-section-h4">Query</h4>
              <pre class="detail-query">{{ detailRecord.query }}</pre>
            </div>
          </a-tab-pane>
          <a-tab-pane key="actions" tab="操作">
            <div class="detail-action-list">
              <a-button
                type="primary"
                class="lucide-icon-btn detail-action-btn"
                :loading="firing"
                @click="handleFire(detailRecord)"
              >
                <template #icon><Flame :size="14" /></template>
                立即触发
              </a-button>
              <a-button
                class="lucide-icon-btn detail-action-btn"
                @click="scheduleStore.openExecutions(detailRecord.id)"
              >
                <template #icon><History :size="14" /></template>
                查看执行历史
              </a-button>
              <a-button
                class="lucide-icon-btn detail-action-btn"
                @click="openEdit(detailRecord)"
              >
                <template #icon><Edit3 :size="14" /></template>
                编辑
              </a-button>
              <a-button
                danger
                class="lucide-icon-btn detail-action-btn"
                @click="handleDelete(detailRecord)"
              >
                <template #icon><Trash2 :size="14" /></template>
                删除
              </a-button>
            </div>
          </a-tab-pane>
        </a-tabs>
      </div>
    </a-drawer>

    <!-- 编辑抽屉 -->
    <a-drawer
      :open="scheduleStore.editDrawer.open"
      :width="560"
      :title="editingId ? '编辑 schedule' : '新建 schedule'"
      :destroy-on-close="false"
      :footer-style="{ textAlign: 'right' }"
      @close="closeEdit"
    >
      <a-form layout="vertical" class="schedule-form">
        <a-form-item label="名称" :validate-status="formErrors.name ? 'error' : ''" required>
          <a-input
            v-model:value="formState.name"
            :maxlength="120"
            placeholder="例如：每日早报"
            show-count
          />
          <div v-if="formErrors.name" class="form-error">{{ formErrors.name }}</div>
        </a-form-item>
        <a-form-item label="描述">
          <a-textarea
            v-model:value="formState.description"
            :rows="2"
            :maxlength="500"
            placeholder="可选，写清楚这个 schedule 干什么"
          />
        </a-form-item>
        <a-form-item
          label="智能体"
          :validate-status="formErrors.agent_slug ? 'error' : ''"
          required
        >
          <a-select
            v-model:value="formState.agent_slug"
            :options="agentOptionsForSelect"
            :loading="agentOptionsLoading"
            placeholder="选择执行任务的智能体"
            show-search
            :filter-option="(input, option) => String(option.label).toLowerCase().includes(input.toLowerCase())"
          />
          <div v-if="formErrors.agent_slug" class="form-error">{{ formErrors.agent_slug }}</div>
        </a-form-item>
        <a-form-item
          label="Query"
          :validate-status="formErrors.query ? 'error' : ''"
          required
        >
          <a-textarea
            v-model:value="formState.query"
            :rows="4"
            placeholder="每次到点会以这段 query 触发智能体"
          />
          <div v-if="formErrors.query" class="form-error">{{ formErrors.query }}</div>
        </a-form-item>
        <a-form-item
          label="Cron 表达式"
          :validate-status="formErrors.cron_expression ? 'error' : ''"
          required
        >
          <a-input
            v-model:value="formState.cron_expression"
            placeholder="例如：0 */1 * * *"
          >
            <template #suffix>
              <a-tooltip
                title="标准 5 段 cron（分 时 日 月 周），由 croniter 解析。"
                placement="topLeft"
              >
                <Info :size="14" class="cron-hint" />
              </a-tooltip>
            </template>
          </a-input>
          <div v-if="formErrors.cron_expression" class="form-error">
            {{ formErrors.cron_expression }}
          </div>
        </a-form-item>
        <a-form-item label="时区" :validate-status="formErrors.timezone ? 'error' : ''" required>
          <a-select
            v-model:value="formState.timezone"
            :options="timezoneOptions"
            show-search
          />
          <div v-if="formErrors.timezone" class="form-error">{{ formErrors.timezone }}</div>
        </a-form-item>
        <a-form-item label="启用">
          <a-switch v-model:checked="formState.enabled" />
          <span class="form-helper">停用后不再触发，但保留配置与历史。</span>
        </a-form-item>
      </a-form>
      <template #footer>
        <a-space>
          <a-button @click="closeEdit">取消</a-button>
          <a-button type="primary" :loading="submitting" @click="submitForm">
            {{ editingId ? '保存' : '创建' }}
          </a-button>
        </a-space>
      </template>
    </a-drawer>

    <!-- 执行历史抽屉 -->
    <a-drawer
      :open="scheduleStore.executionsDrawer.open"
      :width="560"
      :title="`执行历史${scheduleStore.executionsDrawer.schedule ? ` · ${scheduleStore.executionsDrawer.schedule.name}` : ''}`"
      :destroy-on-close="false"
      @close="scheduleStore.closeExecutions"
    >
      <a-spin :spinning="scheduleStore.executionsDrawer.loading">
        <a-empty
          v-if="!scheduleStore.executionsDrawer.loading && !scheduleStore.executionsDrawer.items.length"
          description="暂无执行记录"
        />
        <ul v-else class="execution-list">
          <li
            v-for="exec in scheduleStore.executionsDrawer.items"
            :key="exec.id"
            class="execution-item"
          >
            <div class="execution-header">
              <a-tag :color="statusColorMap[exec.status] || 'default'">
                {{ statusLabels[exec.status] || exec.status }}
              </a-tag>
              <span class="execution-time">
                {{ formatDateTime(exec.fired_at || exec.scheduled_at) }}
              </span>
            </div>
            <dl class="execution-grid">
              <div>
                <dt>计划触发</dt>
                <dd>{{ formatDateTime(exec.scheduled_at) }}</dd>
              </div>
              <div>
                <dt>实际开始</dt>
                <dd>{{ formatDateTime(exec.started_at) }}</dd>
              </div>
              <div>
                <dt>完成时间</dt>
                <dd>{{ formatDateTime(exec.completed_at) }}</dd>
              </div>
              <div v-if="exec.agent_run_id">
                <dt>Run ID</dt>
                <dd><code>{{ exec.agent_run_id }}</code></dd>
              </div>
            </dl>
            <div v-if="exec.result_summary" class="execution-summary">
              <h5>结果摘要</h5>
              <pre>{{ exec.result_summary }}</pre>
            </div>
            <div v-if="exec.error" class="execution-error">
              <h5>错误</h5>
              <pre>{{ exec.error }}</pre>
            </div>
          </li>
        </ul>
      </a-spin>
    </a-drawer>
  </div>
</template>

<style lang="less" scoped>
.schedule-view {
  display: flex;
  flex-direction: column;
  min-height: 100%;
  padding: 24px var(--page-padding, 24px);
  background: var(--gray-0);
  color: var(--gray-1000);
}

.schedule-header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 20px;

  .page-title {
    margin: 0;
    color: var(--gray-1000);
    font-size: 20px;
    font-weight: 600;
  }

  .page-subtitle {
    margin: 6px 0 0;
    color: var(--gray-600);
    font-size: 13px;
    line-height: 1.6;
  }
}

.header-stats {
  display: flex;
  flex-shrink: 0;
  gap: 8px;

  .stat-pill {
    padding: 4px 10px;
    border: 1px solid var(--gray-150);
    border-radius: 999px;
    background: var(--gray-10);
    color: var(--gray-700);
    font-size: 12px;
    line-height: 18px;
  }

  .stat-enabled {
    border-color: var(--color-success-100);
    background: var(--color-success-50);
    color: var(--color-success-700);
  }

  .stat-disabled {
    border-color: var(--gray-200);
    background: var(--gray-50);
    color: var(--gray-600);
  }
}

.permission-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 360px;
}

.schedule-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-bottom: 16px;
}

.schedule-search {
  width: 280px;
}

.schedule-filter {
  width: 120px;
}

.schedule-alert {
  margin-bottom: 12px;
}

.schedule-table {
  background: var(--gray-0);
  border: 1px solid var(--gray-150);
  border-radius: 8px;
}

.cell-name {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.cell-name-icon {
  flex-shrink: 0;
  color: var(--main-color);
}

.cell-name-text {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
}

.cell-name-title {
  color: var(--gray-900);
  font-size: 14px;
  font-weight: 600;
  line-height: 1.4;
}

.cell-name-desc {
  margin-top: 2px;
  color: var(--gray-600);
  font-size: 12px;
  line-height: 1.4;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cell-agent {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.cell-agent-name {
  color: var(--gray-800);
  font-size: 13px;
  font-weight: 500;
  line-height: 1.4;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cell-agent-slug {
  color: var(--gray-500);
  font-size: 11px;
  line-height: 1.4;
}

.cell-cron {
  padding: 2px 6px;
  border: 1px solid var(--gray-150);
  border-radius: 4px;
  background: var(--gray-10);
  color: var(--gray-800);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
}

.cell-time {
  display: flex;
  flex-direction: column;

  & > span:first-child {
    color: var(--gray-800);
    font-size: 13px;
    line-height: 1.4;
  }
}

.cell-time-rel {
  color: var(--gray-500);
  font-size: 11px;
  line-height: 1.4;
}

.row-actions {
  flex-wrap: nowrap;
  justify-content: flex-end;
}

.schedule-empty {
  padding: 60px 0;
}

.detail-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 240px;
}

.detail-content {
  display: flex;
  flex-direction: column;
}

.detail-tabs {
  flex: 1;
}

.detail-section {
  margin-bottom: 16px;
}

.detail-section-title {
  margin: 0 0 4px;
  color: var(--gray-1000);
  font-size: 16px;
  font-weight: 600;
}

.detail-section-desc {
  margin: 0;
  color: var(--gray-600);
  font-size: 13px;
  line-height: 1.6;
}

.detail-section-h4 {
  margin: 0 0 8px;
  color: var(--gray-900);
  font-size: 13px;
  font-weight: 600;
}

.detail-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 12px 16px;
  margin: 0 0 16px;
  padding: 12px 14px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-10);

  > div {
    min-width: 0;
  }

  dt {
    color: var(--gray-500);
    font-size: 11px;
    font-weight: 500;
    line-height: 1.4;
  }

  dd {
    margin: 2px 0 0;
    color: var(--gray-900);
    font-size: 13px;
    line-height: 1.5;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  code {
    padding: 1px 4px;
    border: 1px solid var(--gray-150);
    border-radius: 4px;
    background: var(--gray-0);
    color: var(--gray-800);
    font-size: 12px;
  }
}

.detail-query {
  max-height: 240px;
  margin: 0;
  padding: 10px 12px;
  overflow: auto;
  border: 1px solid var(--gray-150);
  border-radius: 6px;
  background: var(--gray-10);
  color: var(--gray-900);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

.detail-action-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 12px 0;
}

.detail-action-btn {
  justify-content: flex-start;
}

.schedule-form {
  :deep(.ant-form-item) {
    margin-bottom: 16px;
  }

  :deep(.ant-form-item-label > label) {
    color: var(--gray-800);
    font-size: 13px;
    font-weight: 500;
  }
}

.form-helper {
  margin-left: 10px;
  color: var(--gray-500);
  font-size: 12px;
}

.form-error {
  margin-top: 4px;
  color: var(--color-error-700);
  font-size: 12px;
}

.cron-hint {
  color: var(--gray-500);
}

.execution-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.execution-item {
  padding: 12px 14px;
  border: 1px solid var(--gray-150);
  border-radius: 8px;
  background: var(--gray-0);
}

.execution-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}

.execution-time {
  color: var(--gray-500);
  font-size: 12px;
}

.execution-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 8px 12px;
  margin: 0 0 8px;

  > div {
    min-width: 0;
  }

  dt {
    color: var(--gray-500);
    font-size: 11px;
    font-weight: 500;
    line-height: 1.4;
  }

  dd {
    margin: 2px 0 0;
    color: var(--gray-800);
    font-size: 12px;
    line-height: 1.5;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  code {
    padding: 1px 4px;
    border: 1px solid var(--gray-150);
    border-radius: 4px;
    background: var(--gray-10);
    color: var(--gray-800);
    font-size: 11px;
  }
}

.execution-summary,
.execution-error {
  margin-top: 8px;

  h5 {
    margin: 0 0 4px;
    color: var(--gray-700);
    font-size: 12px;
    font-weight: 600;
  }

  pre {
    max-height: 220px;
    margin: 0;
    padding: 8px 10px;
    overflow: auto;
    border: 1px solid var(--gray-150);
    border-radius: 6px;
    background: var(--gray-10);
    color: var(--gray-800);
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 12px;
    line-height: 1.5;
    white-space: pre-wrap;
    word-break: break-word;
  }
}

.execution-error pre {
  border-color: var(--color-error-100);
  background: var(--color-error-10);
  color: var(--color-error-700);
}

@media (max-width: 768px) {
  .schedule-header {
    flex-direction: column;
    align-items: flex-start;
  }

  .schedule-search {
    width: 100%;
  }

  .schedule-filter {
    width: 100%;
  }

  .detail-grid {
    grid-template-columns: 1fr;
  }

  .execution-grid {
    grid-template-columns: 1fr;
  }
}
</style>
