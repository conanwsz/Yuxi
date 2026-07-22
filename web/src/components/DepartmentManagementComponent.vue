<template>
  <div class="department-management">
    <div class="header-section">
      <div>
        <div class="section-title">部门管理</div>
        <p class="section-description">按主体和上下级维护组织架构；管理范围自动覆盖所选部门的整棵子树。</p>
      </div>
      <a-space>
        <a-button class="lucide-icon-btn" :loading="state.refreshing" @click="refresh">
          <template #icon><RefreshCw :size="16" :class="{ spin: state.refreshing }" /></template>
        </a-button>
        <a-button
          v-if="userStore.isSuperAdmin && userStore.hasPermission('departments.create')"
          type="primary"
          class="lucide-icon-btn"
          @click="openCreate(null)"
        >
          <template #icon><Plus :size="16" /></template>
          新建主体
        </a-button>
      </a-space>
    </div>

    <a-alert
      class="scope-hint"
      type="info"
      show-icon
      message="部门成员身份与管理权限相互独立；归属部门不会自动成为该部门管理员。"
    />

    <a-spin :spinning="state.loading">
      <a-alert v-if="state.error" type="error" :message="state.error" show-icon />
      <a-table
        v-else-if="state.tree.length"
        :data-source="state.tree"
        :columns="columns"
        :row-key="(record) => record.id"
        :pagination="false"
        :default-expand-all-rows="true"
        class="department-table"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'name'">
            <div class="name-cell">
              <Building2 v-if="record.parent_id == null" :size="16" />
              <FolderTree v-else :size="16" />
              <span>{{ record.name }}</span>
              <a-tooltip v-if="record.is_system" title="默认部门为系统节点，不能移动或停用">
                <LockKeyhole :size="14" class="system-lock" />
              </a-tooltip>
            </div>
          </template>
          <template v-else-if="column.key === 'path'">
            <span class="path-text">{{ record.path_label }}</span>
          </template>
          <template v-else-if="column.key === 'members'">
            {{ record.direct_user_count }} / {{ record.total_user_count }}
          </template>
          <template v-else-if="column.key === 'status'">
            <a-tag :color="record.status === 'active' ? 'green' : 'default'">
              {{ record.status === 'active' ? '启用' : '已停用' }}
            </a-tag>
          </template>
          <template v-else-if="column.key === 'action'">
            <a-space size="small">
              <a-tooltip v-if="userStore.hasPermission('departments.create')" title="新建子部门">
                <a-button type="text" size="small" class="lucide-icon-btn" @click="openCreate(record)">
                  <Plus :size="15" />
                </a-button>
              </a-tooltip>
              <a-tooltip v-if="userStore.hasPermission('departments.update')" title="编辑部门">
                <a-button type="text" size="small" class="lucide-icon-btn" @click="openEdit(record)">
                  <SquarePen :size="15" />
                </a-button>
              </a-tooltip>
              <a-tooltip
                v-if="record.status === 'active' && userStore.hasPermission('departments.delete')"
                :title="record.is_system ? '系统节点不能停用' : '停用部门'"
              >
                <a-button
                  type="text"
                  size="small"
                  danger
                  class="lucide-icon-btn"
                  :disabled="record.is_system"
                  @click="confirmArchive(record)"
                >
                  <Archive :size="15" />
                </a-button>
              </a-tooltip>
              <a-tooltip
                v-else-if="record.status === 'inactive' && userStore.hasPermission('departments.update')"
                title="恢复部门"
              >
                <a-button type="text" size="small" class="lucide-icon-btn" @click="restore(record)">
                  <RotateCcw :size="15" />
                </a-button>
              </a-tooltip>
            </a-space>
          </template>
        </template>
      </a-table>
      <a-empty v-else description="暂无组织节点" class="empty-state" />
    </a-spin>

    <a-modal
      v-model:open="state.modalVisible"
      :title="state.editId ? '编辑部门' : state.form.parentId ? '新建子部门' : '新建主体'"
      :confirm-loading="state.submitting"
      :mask-closable="false"
      width="560px"
      @ok="submit"
    >
      <a-form layout="vertical">
        <a-form-item label="部门名称" required>
          <a-input v-model:value="state.form.name" :maxlength="50" placeholder="同一上级下名称不能重复" />
        </a-form-item>
        <a-form-item label="上级部门">
          <a-select
            v-model:value="state.form.parentId"
            :disabled="Boolean(state.editRecord?.is_system)"
            allow-clear
            placeholder="留空表示根主体"
          >
            <a-select-option v-for="item in parentOptions" :key="item.id" :value="item.id">
              {{ item.path_label }}
            </a-select-option>
          </a-select>
          <div class="help-text">普通管理员只能在管理范围内创建和移动；节点不能跨主体移动。</div>
        </a-form-item>
        <a-form-item label="排序">
          <a-input-number v-model:value="state.form.sortOrder" :min="0" style="width: 100%" />
        </a-form-item>
        <a-form-item label="描述">
          <a-textarea v-model:value="state.form.description" :rows="3" :maxlength="255" show-count />
        </a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { departmentApi } from '@/apis'
import { useUserStore } from '@/stores/user'
import {
  Archive,
  Building2,
  FolderTree,
  LockKeyhole,
  Plus,
  RefreshCw,
  RotateCcw,
  SquarePen
} from 'lucide-vue-next'

const userStore = useUserStore()
const columns = [
  { title: '名称', key: 'name', width: 240 },
  { title: '完整路径', key: 'path', ellipsis: true },
  { title: '直属 / 全部成员', key: 'members', width: 140, align: 'center' },
  { title: '状态', key: 'status', width: 90, align: 'center' },
  { title: '操作', key: 'action', width: 150, align: 'center' }
]

const state = reactive({
  loading: false,
  refreshing: false,
  submitting: false,
  error: '',
  tree: [],
  flat: [],
  modalVisible: false,
  editId: null,
  editRecord: null,
  form: { name: '', parentId: null, description: '', sortOrder: 0 }
})

const descendantIds = (record) => {
  const ids = new Set([record.id])
  const visit = (nodes) => nodes.forEach((node) => {
    if (ids.has(node.parent_id)) ids.add(node.id)
    visit(node.children || [])
  })
  visit(state.tree)
  return ids
}

const parentOptions = computed(() => {
  const blocked = state.editRecord ? descendantIds(state.editRecord) : new Set()
  return state.flat.filter((item) => item.status === 'active' && !blocked.has(item.id))
})

const load = async () => {
  state.loading = true
  state.error = ''
  try {
    const [tree, flat] = await Promise.all([
      departmentApi.getDepartmentTree(),
      departmentApi.getDepartments()
    ])
    state.tree = tree
    state.flat = flat
  } catch (error) {
    state.error = error.message || '获取组织架构失败'
  } finally {
    state.loading = false
  }
}

const refresh = async () => {
  state.refreshing = true
  await load()
  state.refreshing = false
}

const openCreate = (parent) => {
  state.editId = null
  state.editRecord = null
  state.form = { name: '', parentId: parent?.id ?? null, description: '', sortOrder: 0 }
  state.modalVisible = true
}

const openEdit = (record) => {
  state.editId = record.id
  state.editRecord = record
  state.form = {
    name: record.name,
    parentId: record.parent_id,
    description: record.description || '',
    sortOrder: record.sort_order || 0
  }
  state.modalVisible = true
}

const submit = async () => {
  const name = state.form.name.trim()
  if (!name) return message.error('部门名称不能为空')
  state.submitting = true
  try {
    if (state.editId) {
      await departmentApi.updateDepartment(state.editId, {
        name,
        description: state.form.description.trim() || null,
        sort_order: state.form.sortOrder
      })
      if (!state.editRecord.is_system && state.form.parentId !== state.editRecord.parent_id) {
        if (state.form.parentId == null) throw new Error('暂不支持将现有节点移动为根主体')
        await departmentApi.moveDepartment(state.editId, state.form.parentId)
      }
      message.success('部门已更新')
    } else {
      await departmentApi.createDepartment({
        name,
        parent_id: state.form.parentId,
        description: state.form.description.trim() || null,
        sort_order: state.form.sortOrder
      })
      message.success('部门已创建')
    }
    state.modalVisible = false
    await load()
  } catch (error) {
    message.error(error.message || '保存失败')
  } finally {
    state.submitting = false
  }
}

const confirmArchive = (record) => Modal.confirm({
  title: `停用“${record.name}”`,
  content: '停用前需要先迁移直属成员，并处理所有启用中的子部门。停用后该节点不再产生新的资源访问权限。',
  okText: '停用',
  okType: 'danger',
  cancelText: '取消',
  async onOk() {
    await departmentApi.archiveDepartment(record.id)
    message.success('部门已停用')
    await load()
  }
})

const restore = async (record) => {
  try {
    await departmentApi.restoreDepartment(record.id)
    message.success('部门已恢复')
    await load()
  } catch (error) {
    message.error(error.message || '恢复失败')
  }
}

onMounted(load)
</script>

<style lang="less" scoped>
.department-management {
  .header-section {
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    gap: 16px;
    margin-bottom: 16px;
  }

  .section-title {
    margin: 12px 0;
    color: var(--gray-900);
    font-size: 16px;
    font-weight: 500;
  }

  .section-description,
  .path-text,
  .help-text {
    color: var(--gray-600);
  }

  .section-description {
    margin: 0;
    font-size: 14px;
  }

  .scope-hint {
    margin-bottom: 16px;
  }

  .department-table {
    :deep(.ant-table-thead > tr > th) {
      background: var(--gray-50);
    }
  }

  .name-cell {
    display: flex;
    align-items: center;
    gap: 7px;
    color: var(--gray-900);
    font-weight: 500;
  }

  .system-lock {
    color: var(--gray-500);
  }

  .help-text {
    margin-top: 4px;
    font-size: 12px;
  }

  .empty-state {
    padding: 56px 0;
  }

  .spin {
    animation: spin 1s linear infinite;
  }
}

@keyframes spin {
  to { transform: rotate(360deg); }
}
</style>
