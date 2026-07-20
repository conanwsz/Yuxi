<template>
  <div class="permission-management">
    <div class="permission-header">
      <div class="section-title">权限管理</div>
      <a-button type="primary" @click="openCreate">新建角色</a-button>
    </div>

    <a-spin :spinning="loading">
      <div class="permission-layout">
        <div class="role-list">
          <button
            v-for="role in roles"
            :key="role.key"
            type="button"
            class="role-item"
            :class="{ active: selectedRole?.key === role.key }"
            @click="selectRole(role)"
          >
            <span class="role-name">{{ role.name }}</span>
            <span class="role-meta">{{ role.user_count }} 人</span>
            <span v-if="role.is_system" class="system-badge">系统</span>
          </button>
        </div>

        <div v-if="selectedRole" class="matrix-panel">
          <div class="role-editor">
            <div>
              <div class="role-editor-title">{{ selectedRole.name }}</div>
              <div class="role-key">{{ selectedRole.key }}</div>
            </div>
            <div class="role-actions">
              <a-button
                v-if="!selectedRole.is_system"
                danger
                :disabled="!selectedRole.deletable"
                @click="removeSelectedRole"
              >
                删除角色
              </a-button>
              <a-button
                type="primary"
                :disabled="!selectedRole.editable"
                :loading="saving"
                @click="savePermissions"
              >
                保存权限
              </a-button>
            </div>
          </div>

          <a-alert
            v-if="selectedRole.key === 'superadmin'"
            type="info"
            show-icon
            message="超级管理员始终拥有全部权限，不能修改。"
          />
          <a-alert
            v-else-if="!selectedRole.is_system && selectedRole.user_count > 0"
            type="warning"
            show-icon
            :message="`该角色仍被 ${selectedRole.user_count} 个用户使用，不能删除。`"
          />

          <div v-if="selectedRole.key !== 'superadmin'" class="role-fields">
            <a-form layout="vertical">
              <a-form-item label="角色名称">
                <a-input v-model:value="draftName" :disabled="selectedRole.is_system" maxlength="100" />
              </a-form-item>
              <a-form-item label="描述">
                <a-textarea v-model:value="draftDescription" maxlength="255" />
              </a-form-item>
            </a-form>
          </div>

          <div class="matrix-groups">
            <section v-for="group in groups" :key="group.key" class="matrix-group">
              <div class="group-title">{{ group.name }}</div>
              <div class="permission-grid">
                <label
                  v-for="permission in group.permissions"
                  :key="permission.key"
                  class="permission-item"
                >
                  <a-checkbox
                    :checked="draftPermissions.includes(permission.key)"
                    :disabled="!selectedRole.editable"
                    @change="togglePermission(permission.key, $event.target.checked)"
                  />
                  <span class="permission-name">{{ permission.name }}</span>
                  <span class="permission-key">{{ permission.key }}</span>
                </label>
              </div>
            </section>
          </div>
        </div>
        <a-empty v-else description="暂无角色" class="matrix-panel" />
      </div>
    </a-spin>

    <a-modal v-model:open="createVisible" title="新建角色" :confirm-loading="saving" @ok="createRole">
      <a-form layout="vertical">
        <a-form-item label="角色名称" required>
          <a-input v-model:value="createForm.name" maxlength="100" />
        </a-form-item>
        <a-form-item label="角色 Key" required>
          <a-input v-model:value="createForm.key" placeholder="例如 reviewer" maxlength="64" />
        </a-form-item>
        <a-form-item label="描述">
          <a-textarea v-model:value="createForm.description" maxlength="255" />
        </a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { Modal, message } from 'ant-design-vue'
import { roleApi } from '@/apis/role_api'

const loading = ref(false)
const saving = ref(false)
const roles = ref([])
const groups = ref([])
const selectedRole = ref(null)
const draftPermissions = ref([])
const draftName = ref('')
const draftDescription = ref('')
const createVisible = ref(false)
const createForm = reactive({ key: '', name: '', description: '' })

const selectRole = (role) => {
  selectedRole.value = role
  draftPermissions.value = [...(role.permissions || [])]
  draftName.value = role.name || ''
  draftDescription.value = role.description || ''
}

const loadData = async (preferredKey) => {
  loading.value = true
  try {
    const [roleResult, catalogResult] = await Promise.all([
      roleApi.getRoles(),
      roleApi.getPermissionCatalog()
    ])
    roles.value = roleResult.roles || []
    groups.value = catalogResult.groups || []
    selectRole(roles.value.find((role) => role.key === preferredKey) || roles.value[0] || null)
  } catch (error) {
    message.error(error.message || '加载权限矩阵失败')
  } finally {
    loading.value = false
  }
}

const togglePermission = (key, checked) => {
  const values = new Set(draftPermissions.value)
  checked ? values.add(key) : values.delete(key)
  draftPermissions.value = [...values]
}

const savePermissions = async () => {
  if (!selectedRole.value?.editable) return
  saving.value = true
  try {
    const payload = {
      permissions: draftPermissions.value,
      description: draftDescription.value.trim() || null
    }
    if (!selectedRole.value.is_system) payload.name = draftName.value.trim()
    await roleApi.updateRole(selectedRole.value.key, payload)
    message.success('角色权限已保存')
    await loadData(selectedRole.value.key)
  } catch (error) {
    message.error(error.message || '保存角色权限失败')
  } finally {
    saving.value = false
  }
}

const openCreate = () => {
  Object.assign(createForm, { key: '', name: '', description: '' })
  createVisible.value = true
}

const createRole = async () => {
  if (!createForm.key.trim() || !createForm.name.trim()) {
    message.error('请填写角色名称和 Key')
    return
  }
  saving.value = true
  try {
    const result = await roleApi.createRole({
      key: createForm.key.trim(),
      name: createForm.name.trim(),
      description: createForm.description.trim(),
      permissions: []
    })
    createVisible.value = false
    message.success('角色已创建')
    await loadData(result.role.key)
  } catch (error) {
    message.error(error.message || '创建角色失败')
  } finally {
    saving.value = false
  }
}

const removeSelectedRole = () => {
  if (!selectedRole.value?.deletable) return
  Modal.confirm({
    title: `删除角色“${selectedRole.value.name}”？`,
    content: '该操作不可撤销。',
    async onOk() {
      await roleApi.deleteRole(selectedRole.value.key)
      message.success('角色已删除')
      await loadData()
    }
  })
}

onMounted(() => loadData())
</script>

<style scoped lang="less">
.permission-management {
  .permission-header,
  .role-editor {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
  }

  .section-title,
  .role-editor-title {
    color: var(--gray-900);
    font-size: 18px;
    font-weight: 600;
  }

  .role-key,
  .permission-key {
    color: var(--gray-600);
  }

  .permission-layout {
    display: grid;
    grid-template-columns: 210px minmax(0, 1fr);
    gap: 12px;
    margin-top: 12px;
  }

  .role-list,
  .matrix-panel {
    border: 1px solid var(--gray-200);
    border-radius: 8px;
    background: var(--gray-0);
  }

  .role-list {
    padding: 8px;
  }

  .role-item {
    display: grid;
    grid-template-columns: 1fr auto;
    width: 100%;
    padding: 10px;
    border: 0;
    border-radius: 6px;
    background: transparent;
    color: var(--gray-900);
    text-align: left;
    cursor: pointer;

    &.active {
      background: var(--main-40);
    }
  }

  .role-meta,
  .system-badge {
    color: var(--gray-600);
    font-size: 12px;
  }

  .matrix-panel {
    padding: 16px;
  }

  .role-actions {
    display: flex;
    gap: 8px;
  }

  .matrix-groups {
    display: grid;
    gap: 0;
    margin-top: 16px;
    border: 1px solid var(--gray-200);
    border-radius: 8px;
    overflow: hidden;
  }

  .role-fields {
    margin-top: 12px;

    :deep(.ant-form) {
      display: grid;
      grid-template-columns: minmax(180px, 0.7fr) minmax(280px, 1.3fr);
      gap: 12px;
    }

    :deep(.ant-form-item) {
      margin-bottom: 0;
    }
  }

  .matrix-group {
    display: grid;
    grid-template-columns: 120px minmax(0, 1fr);

    & + .matrix-group {
      border-top: 1px solid var(--gray-200);
    }
  }

  .group-title {
    padding: 12px;
    border-right: 1px solid var(--gray-200);
    background: var(--gray-25);
    color: var(--gray-900);
    font-weight: 600;
  }

  .permission-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .permission-item {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
    min-height: 40px;
    padding: 8px 12px;
    border-bottom: 1px solid var(--gray-150);

    &:nth-child(odd) {
      border-right: 1px solid var(--gray-150);
    }

    &:nth-last-child(-n + 2) {
      border-bottom: 0;
    }
  }

  .permission-name {
    color: var(--gray-800);
    white-space: nowrap;
  }

  .permission-key {
    margin-left: auto;
    overflow: hidden;
    font-size: 11px;
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  @media (max-width: 720px) {
    .permission-layout,
    .role-fields :deep(.ant-form),
    .matrix-group,
    .permission-grid {
      grid-template-columns: 1fr;
    }

    .group-title {
      border-right: 0;
      border-bottom: 1px solid var(--gray-200);
    }

    .permission-item {
      border-right: 0 !important;

      &:nth-last-child(2) {
        border-bottom: 1px solid var(--gray-150);
      }
    }
  }
}
</style>
