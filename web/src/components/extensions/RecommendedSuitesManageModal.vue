<template>
  <a-modal
    :open="props.open"
    @update:open="(val) => emit('update:open', val)"
    :footer="null"
    width="900px"
    :destroy-on-close="true"
    :mask-closable="false"
    :keyboard="false"
    :closable="!formLoading"
    class="recommended-suites-manage-modal"
    title="管理推荐技能套件"
    @cancel="handleClose"
  >
    <a-tabs v-model:activeKey="activeTab" class="manage-tabs">
      <!-- 套件列表 -->
      <a-tab-pane key="list" tab="套件列表">
        <div class="list-toolbar">
          <a-button type="primary" @click="openCreateForm">
            <Plus :size="14" />
            <span>新建套件</span>
          </a-button>
        </div>
        <a-table
          :data-source="adminSuites"
          :columns="listColumns"
          :loading="listLoading"
          :pagination="false"
          row-key="id"
          size="middle"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'enabled'">
              <a-tag :color="record.enabled ? 'green' : 'default'">
                {{ record.enabled ? '启用' : '已禁用' }}
              </a-tag>
            </template>
            <template v-else-if="column.key === 'members'">
              {{ (record.members || []).length }} 个
            </template>
            <template v-else-if="column.key === 'actions'">
              <a-space>
                <a-button size="small" type="link" @click="openEditForm(record)">编辑</a-button>
                <a-popconfirm
                  :title="record.enabled ? '确认停用此套件？' : '确认启用此套件？'"
                  @confirm="handleToggleEnabled(record)"
                >
                  <a-button size="small" type="link">
                    {{ record.enabled ? '停用' : '启用' }}
                  </a-button>
                </a-popconfirm>
                <a-popconfirm
                  title="确认删除此套件？已装用户的 skill 不受影响。"
                  @confirm="handleDelete(record)"
                >
                  <a-button size="small" type="link" danger>删除</a-button>
                </a-popconfirm>
              </a-space>
            </template>
          </template>
        </a-table>
        <a-empty v-if="!listLoading && adminSuites.length === 0" description="暂无推荐套件" />
      </a-tab-pane>

      <!-- 新建/编辑 -->
      <a-tab-pane :key="formTabKey" :tab="editingSuiteId ? '编辑套件' : '新建套件'">
        <a-form layout="vertical" :model="form" class="suite-form">
          <a-form-item label="slug" required>
            <a-input
              v-model:value="form.slug"
              placeholder="稳定 ID，例如 anthropic-documents"
              :disabled="!!editingSuiteId"
            />
            <div class="field-hint">创建后不可修改。只能包含小写字母、数字、连字符和下划线。</div>
          </a-form-item>

          <a-form-item label="名称" required>
            <a-input v-model:value="form.name" placeholder="展示名" />
          </a-form-item>

          <a-form-item label="提供方" required>
            <a-input v-model:value="form.provider" placeholder="如 Anthropic" />
          </a-form-item>

          <a-form-item label="描述">
            <a-textarea v-model:value="form.description" :rows="2" placeholder="卡片描述（可空）" />
          </a-form-item>

          <a-form-item label="source" required>
            <a-input
              v-model:value="form.source"
              placeholder="GitHub owner/repo、完整 URL 或 ModelScope 单 skill URL"
            />
            <div class="field-hint">用户点击「查看并安装」时按此 source 走远程安装流程。</div>
          </a-form-item>

          <a-row :gutter="12">
            <a-col :span="12">
              <a-form-item label="排序">
                <a-input-number v-model:value="form.sort_order" :min="0" style="width: 100%" />
              </a-form-item>
            </a-col>
            <a-col :span="12">
              <a-form-item label="启用">
                <a-switch v-model:checked="form.enabled" />
              </a-form-item>
            </a-col>
          </a-row>

          <a-divider />

          <div class="members-section">
            <div class="members-toolbar">
              <span class="members-label">成员（至少 1 个）</span>
              <a-space>
                <a-upload
                  accept=".zip"
                  :show-upload-list="false"
                  :before-upload="beforeZipUpload"
                  :custom-request="handleZipUpload"
                >
                  <a-button :loading="zipParsing">
                    <Upload :size="14" />
                    <span>上传 zip 自动填充</span>
                  </a-button>
                </a-upload>
                <a-button @click="addManualMember">
                  <Plus :size="14" />
                  <span>手动添加</span>
                </a-button>
              </a-space>
            </div>
            <a-alert
              v-if="zipError"
              type="error"
              :message="zipError"
              show-icon
              style="margin-bottom: 8px"
            />
            <div class="field-hint" style="margin-bottom: 8px">
              zip 格式：顶层不放 SKILL.md；按顶层子目录组织，每个子目录 1 个 SKILL.md。
            </div>
            <a-table
              :data-source="form.members"
              :columns="memberColumns"
              :pagination="false"
              row-key="tmpKey"
              size="small"
            >
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === 'slug'">
                  <a-input v-model:value="record.slug" size="small" placeholder="slug" />
                </template>
                <template v-else-if="column.key === 'name'">
                  <a-input v-model:value="record.name" size="small" placeholder="展示名" />
                </template>
                <template v-else-if="column.key === 'description'">
                  <a-input v-model:value="record.description" size="small" placeholder="描述" />
                </template>
                <template v-else-if="column.key === 'actions'">
                  <a-button size="small" type="link" danger @click="removeMember(record.tmpKey)">
                    移除
                  </a-button>
                </template>
              </template>
            </a-table>
          </div>

          <div class="form-footer">
            <a-button @click="activeTab = 'list'">取消</a-button>
            <a-button type="primary" :loading="formLoading" @click="handleSubmit">
              {{ editingSuiteId ? '保存' : '创建' }}
            </a-button>
          </div>
        </a-form>
      </a-tab-pane>
    </a-tabs>
  </a-modal>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { message, Modal as AntModal } from 'ant-design-vue'
import { Plus, Upload } from 'lucide-vue-next'
import { skillApi } from '@/apis/skill_api'

const props = defineProps({
  open: { type: Boolean, default: false }
})

const emit = defineEmits(['update:open', 'close', 'updated'])

const activeTab = ref('list')
const listLoading = ref(false)
const adminSuites = ref([])
const formLoading = ref(false)
const zipParsing = ref(false)
const zipError = ref('')
const editingSuiteId = ref(null)
const formTabKey = computed(() => `form-${editingSuiteId.value || 'new'}`)

const blankForm = () => ({
  slug: '',
  name: '',
  provider: '',
  description: '',
  source: '',
  sort_order: 0,
  enabled: true,
  members: []
})

const form = reactive(blankForm())

const listColumns = [
  { title: '名称', dataIndex: 'name', key: 'name' },
  { title: '提供方', dataIndex: 'provider', key: 'provider' },
  { title: '状态', dataIndex: 'enabled', key: 'enabled' },
  { title: '成员', key: 'members' },
  { title: '排序', dataIndex: 'sort_order', key: 'sort_order', width: 80 },
  { title: '操作', key: 'actions', width: 200 }
]

const memberColumns = [
  { title: 'slug', key: 'slug', width: 180 },
  { title: '名称', key: 'name', width: 180 },
  { title: '描述', key: 'description' },
  { title: '操作', key: 'actions', width: 80 }
]

let tmpKeyCounter = 0
const nextTmpKey = () => `tmp-${++tmpKeyCounter}`

const fetchAdminList = async () => {
  listLoading.value = true
  try {
    const res = await skillApi.listRecommendedSuitesAdmin()
    adminSuites.value = res?.data || []
  } catch (error) {
    message.error(error?.response?.data?.detail || '获取套件列表失败')
    adminSuites.value = []
  } finally {
    listLoading.value = false
  }
}

const resetForm = () => {
  Object.assign(form, blankForm())
  editingSuiteId.value = null
  zipError.value = ''
}

const openCreateForm = () => {
  resetForm()
  activeTab.value = 'form-new'
}

const openEditForm = (suite) => {
  resetForm()
  editingSuiteId.value = suite.id
  form.slug = suite.slug
  form.name = suite.name
  form.provider = suite.provider
  form.description = suite.description || ''
  form.source = suite.source
  form.sort_order = suite.sort_order ?? 0
  form.enabled = suite.enabled !== false
  form.members = (suite.members || []).map((m) => ({
    ...m,
    tmpKey: nextTmpKey()
  }))
  activeTab.value = `form-${suite.id}`
}

const addManualMember = () => {
  form.members.push({ slug: '', name: '', description: '', tmpKey: nextTmpKey() })
}

const removeMember = (tmpKey) => {
  form.members = form.members.filter((m) => m.tmpKey !== tmpKey)
}

const beforeZipUpload = (file) => {
  if (!file.name.toLowerCase().endsWith('.zip')) {
    message.error('仅支持 .zip 压缩包')
    return false
  }
  return true
}

const handleZipUpload = async ({ file, onSuccess, onError }) => {
  zipParsing.value = true
  zipError.value = ''
  try {
    const res = await skillApi.prepareSuiteUpload(file)
    const items = res?.data?.items || []
    if (items.length === 0) {
      throw new Error('上传的 zip 未解析出任何 skill')
    }
    // 替换现有 members（也可追加，按当前实现：替换以避免重复）
    form.members = items.map((item) => ({
      slug: item.slug,
      name: item.name,
      description: item.description || '',
      tmpKey: nextTmpKey()
    }))
    message.success(`已解析 ${items.length} 个 skill`)
    onSuccess?.(res)
  } catch (error) {
    const detail = error?.response?.data?.detail || error?.message || '解析 zip 失败'
    zipError.value = detail
    onError?.(error)
  } finally {
    zipParsing.value = false
  }
}

const handleSubmit = async () => {
  // 基础校验
  if (!form.slug.trim()) {
    message.error('slug 不能为空')
    return
  }
  if (!form.name.trim() || !form.provider.trim() || !form.source.trim()) {
    message.error('名称、提供方、source 均为必填')
    return
  }
  const cleanedMembers = form.members
    .map((m) => ({
      slug: (m.slug || '').trim(),
      name: (m.name || '').trim(),
      description: (m.description || '').trim()
    }))
    .filter((m) => m.slug && m.name)
  if (cleanedMembers.length === 0) {
    message.error('至少需要 1 个有效成员（slug 与名称都必填）')
    return
  }
  // 重复 slug 校验
  const seen = new Set()
  for (const m of cleanedMembers) {
    if (seen.has(m.slug)) {
      message.error(`成员 slug 重复：${m.slug}`)
      return
    }
    seen.add(m.slug)
  }

  formLoading.value = true
  const payload = {
    slug: form.slug.trim(),
    name: form.name.trim(),
    provider: form.provider.trim(),
    description: form.description.trim(),
    source: form.source.trim(),
    sort_order: form.sort_order,
    enabled: form.enabled,
    members: cleanedMembers.map((m, idx) => ({ ...m, sort_order: idx }))
  }
  try {
    if (editingSuiteId.value) {
      await skillApi.updateRecommendedSuite(editingSuiteId.value, payload)
      message.success('已保存')
    } else {
      await skillApi.createRecommendedSuite(payload)
      message.success('已创建')
    }
    resetForm()
    activeTab.value = 'list'
    await fetchAdminList()
    emit('updated')
  } catch (error) {
    const detail = error?.response?.data?.detail || error?.message || '保存失败'
    message.error(detail)
  } finally {
    formLoading.value = false
  }
}

const handleToggleEnabled = async (suite) => {
  try {
    await skillApi.setRecommendedSuiteEnabled(suite.id, !suite.enabled)
    message.success(suite.enabled ? '已停用' : '已启用')
    await fetchAdminList()
    emit('updated')
  } catch (error) {
    message.error(error?.response?.data?.detail || '操作失败')
  }
}

const handleDelete = (suite) => {
  AntModal.confirm({
    title: '确认删除此套件？',
    content: `「${suite.name}」将从推荐列表移除；已装用户的 skill 不受影响。`,
    okText: '确认删除',
    okType: 'danger',
    cancelText: '取消',
    onOk: async () => {
      try {
        await skillApi.deleteRecommendedSuite(suite.id)
        message.success('已删除')
        await fetchAdminList()
        emit('updated')
      } catch (error) {
        message.error(error?.response?.data?.detail || '删除失败')
      }
    }
  })
}

const handleClose = () => {
  if (formLoading.value) return
  emit('update:open', false)
  emit('close')
}

watch(
  () => props.open,
  (val) => {
    if (val) {
      resetForm()
      activeTab.value = 'list'
      fetchAdminList()
    }
  },
  { immediate: true }
)
</script>

<style lang="less" scoped>
@import '@/assets/css/extensions.less';

.recommended-suites-manage-modal {
  .manage-tabs {
    min-height: 420px;
  }

  .list-toolbar {
    display: flex;
    justify-content: flex-end;
    margin-bottom: 12px;
  }

  .suite-form {
    .field-hint {
      margin-top: 4px;
      color: var(--gray-500);
      font-size: 12px;
    }

    .members-section {
      .members-toolbar {
        display: flex;
        align-items: center;
        justify-content: space-between;
        margin-bottom: 8px;
      }
      .members-label {
        font-weight: 600;
      }
    }

    .form-footer {
      display: flex;
      justify-content: flex-end;
      gap: 8px;
      margin-top: 16px;
    }
  }
}
</style>
