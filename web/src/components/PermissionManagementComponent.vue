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
            v-for="role in visibleRoles"
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
              <a-button type="primary" :disabled="!selectedRole.editable" :loading="saving" @click="saveRole">
                保存权限
              </a-button>
            </div>
          </div>

          <a-alert
            v-if="!selectedRole.is_system && selectedRole.user_count > 0"
            type="warning"
            show-icon
            :message="`该角色仍被 ${selectedRole.user_count} 个用户使用，不能删除。`"
          />

          <div class="role-fields">
            <a-form layout="vertical">
              <a-form-item label="角色名称">
                <a-input v-model:value="draftName" :disabled="selectedRole.is_system" maxlength="100" />
              </a-form-item>
              <a-form-item label="描述">
                <a-textarea v-model:value="draftDescription" maxlength="255" />
              </a-form-item>
            </a-form>
          </div>

          <div class="editor-segment">
            <a-segmented v-model:value="editorMode" :options="editorModeOptions" block />
          </div>

          <div v-if="editorMode === 'permissions'" class="matrix-groups">
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

          <div v-else class="resource-groups">
            <section v-for="group in resourceGroups" :key="group.key" class="resource-group">
              <div class="resource-group-header">
                <div>
                  <div class="group-heading">{{ group.title }}</div>
                  <div class="group-subtitle">{{ getResourceSummary(group.key) }}</div>
                </div>
                <a-radio-group
                  :value="draftResourceAccess[group.key].mode"
                  size="small"
                  button-style="solid"
                  @change="updateResourceMode(group.key, $event.target.value)"
                >
                  <a-radio-button
                    v-for="option in resourceModeOptions"
                    :key="option.value"
                    :value="option.value"
                  >
                    {{ option.label }}
                  </a-radio-button>
                </a-radio-group>
              </div>

              <div class="resource-toolbar">
                <a-input
                  v-model:value="resourceSearch[group.key]"
                  :placeholder="`搜索${group.title}`"
                  allow-clear
                />
              </div>

              <a-alert
                v-if="group.key === 'models' && missingModelDefaultTypes.length"
                type="warning"
                show-icon
                :message="`请为以下模型类型选择默认模型：${missingModelDefaultLabels}`"
              />

              <template v-if="group.key === 'models'">
                <div v-if="filteredModelSections.length" class="model-sections">
                  <section v-for="section in filteredModelSections" :key="section.type" class="model-section">
                    <div class="model-section-header">
                      <div>
                        <div class="model-section-title">{{ section.label }}</div>
                        <div class="model-section-meta">{{ section.items.length }} 个模型</div>
                      </div>
                      <a-select
                        v-if="draftResourceAccess.models.mode === 'selected' && section.defaultOptions.length"
                        :value="draftResourceAccess.models.defaults[section.type]"
                        class="default-model-select"
                        :options="section.defaultOptions"
                        placeholder="选择默认模型"
                        allow-clear
                        @change="updateModelDefault(section.type, $event)"
                      />
                    </div>

                    <div class="provider-sections">
                      <section
                        v-for="provider in section.providers"
                        :key="provider.provider_id || provider.provider_name"
                        class="provider-section"
                      >
                        <div class="provider-title">{{ provider.provider_name }}</div>
                        <div class="resource-entry-list">
                          <label
                            v-for="item in provider.items"
                            :key="item.spec"
                            class="resource-entry"
                            :class="{ disabled: !item.enabled }"
                          >
                            <a-checkbox
                              :checked="isResourceChecked('models', item.spec)"
                              :disabled="
                                !selectedRole.editable || draftResourceAccess.models.mode !== 'selected'
                              "
                              @change="toggleResourceItem('models', item.spec, $event.target.checked)"
                            />
                            <div class="entry-main">
                              <div class="entry-title-row">
                                <span class="entry-title">{{ item.name }}</span>
                                <span v-if="!item.enabled" class="entry-tag warning">未启用</span>
                                <span
                                  v-if="draftResourceAccess.models.defaults[section.type] === item.spec"
                                  class="entry-tag success"
                                >
                                  默认
                                </span>
                              </div>
                              <div class="entry-description">{{ item.description || item.spec }}</div>
                            </div>
                            <span class="entry-key">{{ item.spec }}</span>
                          </label>
                        </div>
                      </section>
                    </div>
                  </section>
                </div>
                <a-empty v-else description="暂无可展示的模型" />
              </template>

              <template v-else>
                <div v-if="filteredCatalogEntries(group.key).length" class="resource-entry-list">
                  <label
                    v-for="item in filteredCatalogEntries(group.key)"
                    :key="item.key"
                    class="resource-entry"
                    :class="{ disabled: !item.enabled }"
                  >
                    <a-checkbox
                      :checked="isResourceChecked(group.key, item.key)"
                      :disabled="!selectedRole.editable || draftResourceAccess[group.key].mode !== 'selected'"
                      @change="toggleResourceItem(group.key, item.key, $event.target.checked)"
                    />
                    <div class="entry-main">
                      <div class="entry-title-row">
                        <span class="entry-title">{{ item.name }}</span>
                        <span v-if="!item.enabled" class="entry-tag warning">未启用</span>
                      </div>
                      <div class="entry-description">{{ item.description || item.key }}</div>
                    </div>
                    <span class="entry-key">{{ item.key }}</span>
                  </label>
                </div>
                <a-empty v-else description="暂无可展示的资源" />
              </template>

              <div v-if="offlineEntries(group.key).length" class="offline-section">
                <div class="offline-title">已下线但仍被授权</div>
                <div class="offline-tags">
                  <a-tag
                    v-for="entry in offlineEntries(group.key)"
                    :key="entry.key"
                    :closable="selectedRole.editable && draftResourceAccess[group.key].mode === 'selected'"
                    color="orange"
                    @close.prevent="removeOfflineEntry(group.key, entry.key)"
                  >
                    {{ entry.label }}
                  </a-tag>
                </div>
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
import { computed, onMounted, reactive, ref } from 'vue'
import { Modal, message } from 'ant-design-vue'

import { roleApi } from '@/apis/role_api'
import {
  MODEL_TYPES,
  RESOURCE_GROUP_CONFIG,
  RESOURCE_MODE_OPTIONS,
  cloneResourceAccess,
  createAllResourceAccess,
  createEmptyResourceAccess,
  normalizeResourceAccess,
  normalizeResourceCatalog
} from '@/utils/roleResourceAccess'

const editorModeOptions = [
  { label: '功能权限', value: 'permissions' },
  { label: '数据权限', value: 'resources' }
]
const modelTypeLabels = {
  chat: 'Chat',
  embedding: 'Embedding',
  rerank: 'Rerank'
}

const loading = ref(false)
const saving = ref(false)
const editorMode = ref('permissions')
const roles = ref([])
const groups = ref([])
const resourceCatalog = ref(normalizeResourceCatalog())
const selectedRole = ref(null)
const draftPermissions = ref([])
const draftResourceAccess = ref(createAllResourceAccess())
const draftName = ref('')
const draftDescription = ref('')
const createVisible = ref(false)
const createForm = reactive({ key: '', name: '', description: '' })
const resourceSearch = reactive({ models: '', tools: '', mcp_servers: '' })

const visibleRoles = computed(() => roles.value.filter((role) => role.key !== 'superadmin'))
const resourceGroups = Object.entries(RESOURCE_GROUP_CONFIG).map(([key, config]) => ({ key, ...config }))
const resourceModeOptions = RESOURCE_MODE_OPTIONS

const filteredCatalogEntries = (groupKey) => {
  const keyword = resourceSearch[groupKey]?.trim().toLowerCase()
  const entries = resourceCatalog.value[groupKey] || []
  if (!keyword) return entries
  return entries.filter((item) =>
    [item.name, item.key, item.description].some((field) => String(field || '').toLowerCase().includes(keyword))
  )
}

const filteredModelSections = computed(() => {
  const keyword = resourceSearch.models?.trim().toLowerCase()
  return MODEL_TYPES.map((type) => {
    const items = resourceCatalog.value.models
      .filter((item) => item.type === type)
      .filter((item) => {
        if (!keyword) return true
        return [item.name, item.spec, item.provider_name, item.description].some((field) =>
          String(field || '').toLowerCase().includes(keyword)
        )
      })
      .sort((a, b) => {
        const providerCompare = (a.provider_name || a.provider_id).localeCompare(
          b.provider_name || b.provider_id
        )
        if (providerCompare !== 0) return providerCompare
        return a.name.localeCompare(b.name)
      })

    const providerMap = new Map()
    for (const item of items) {
      const providerKey = item.provider_id || item.provider_name
      if (!providerMap.has(providerKey)) {
        providerMap.set(providerKey, {
          provider_id: item.provider_id,
          provider_name: item.provider_name || item.provider_id,
          items: []
        })
      }
      providerMap.get(providerKey).items.push(item)
    }

    return {
      type,
      label: modelTypeLabels[type] || type,
      items,
      providers: [...providerMap.values()],
      defaultOptions: getModelDefaultOptions(type)
    }
  }).filter((section) => section.items.length)
})

const missingModelDefaultTypes = computed(() => {
  if (draftResourceAccess.value.models.mode !== 'selected') return []
  return MODEL_TYPES.filter((type) => {
    const allowedModels = getAllowedCatalogModelsByType(type)
    if (!allowedModels.length) return false
    return !draftResourceAccess.value.models.defaults[type]
  })
})

const missingModelDefaultLabels = computed(() =>
  missingModelDefaultTypes.value.map((type) => modelTypeLabels[type] || type).join('、')
)

const normalizeRoleDraft = (role) => {
  const fallback = role?.resource_access ? createAllResourceAccess() : createAllResourceAccess()
  return normalizeResourceAccess(role?.resource_access, fallback)
}

const selectRole = (role) => {
  selectedRole.value = role
  draftPermissions.value = [...(role.permissions || [])]
  draftResourceAccess.value = cloneResourceAccess(normalizeRoleDraft(role))
  draftName.value = role.name || ''
  draftDescription.value = role.description || ''
  Object.assign(resourceSearch, { models: '', tools: '', mcp_servers: '' })
}

const loadData = async (preferredKey) => {
  loading.value = true
  try {
    const [roleResult, catalogResult, resourceResult] = await Promise.all([
      roleApi.getRoles(),
      roleApi.getPermissionCatalog(),
      roleApi.getResourceCatalog()
    ])
    roles.value = roleResult.roles || []
    groups.value = catalogResult.groups || []
    resourceCatalog.value = normalizeResourceCatalog(resourceResult)
    const nextRole =
      visibleRoles.value.find((role) => role.key === preferredKey) ||
      visibleRoles.value.find((role) => role.key === selectedRole.value?.key) ||
      visibleRoles.value[0] ||
      null
    if (nextRole) selectRole(nextRole)
    else selectedRole.value = null
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

const updateResourceMode = (groupKey, mode) => {
  draftResourceAccess.value[groupKey].mode = mode
}

const isResourceChecked = (groupKey, key) => {
  const group = draftResourceAccess.value[groupKey]
  if (!group) return false
  if (group.mode === 'all') return true
  if (group.mode === 'none') return false
  return group.allowed.includes(key)
}

const toggleResourceItem = (groupKey, key, checked) => {
  const values = new Set(draftResourceAccess.value[groupKey].allowed)
  checked ? values.add(key) : values.delete(key)
  draftResourceAccess.value[groupKey].allowed = [...values]
  if (groupKey === 'models' && !checked) {
    MODEL_TYPES.forEach((type) => {
      if (draftResourceAccess.value.models.defaults[type] === key) {
        draftResourceAccess.value.models.defaults[type] = null
      }
    })
  }
}

const getAllowedCatalogModelsByType = (type) => {
  const allowed = new Set(draftResourceAccess.value.models.allowed)
  return resourceCatalog.value.models.filter((item) => item.type === type && allowed.has(item.spec))
}

const getModelDefaultOptions = (type) =>
  getAllowedCatalogModelsByType(type).map((item) => ({
    label: `${item.name} · ${item.provider_name || item.provider_id}`,
    value: item.spec
  }))

const updateModelDefault = (type, value) => {
  draftResourceAccess.value.models.defaults[type] = value || null
}

const offlineEntries = (groupKey) => {
  const group = draftResourceAccess.value[groupKey]
  if (!group || group.mode !== 'selected') return []
  const currentKeys = new Set((resourceCatalog.value[groupKey] || []).map((item) => item.key || item.spec))
  if (groupKey === 'models') {
    return group.allowed
      .filter((key) => !currentKeys.has(key))
      .map((key) => ({ key, label: key }))
  }
  return group.allowed.filter((key) => !currentKeys.has(key)).map((key) => ({ key, label: key }))
}

const removeOfflineEntry = (groupKey, key) => {
  toggleResourceItem(groupKey, key, false)
}

const getResourceSummary = (groupKey) => {
  const group = draftResourceAccess.value[groupKey]
  const catalogCount = (resourceCatalog.value[groupKey] || []).length
  if (group.mode === 'all') return `全部资源 · ${catalogCount} 项`
  if (group.mode === 'none') return '无权限'
  const offlineCount = offlineEntries(groupKey).length
  const allowedCount = group.allowed.length
  return offlineCount ? `已授权 ${allowedCount} 项（含 ${offlineCount} 项下线）` : `已授权 ${allowedCount} 项`
}

const buildResourceAccessPayload = () => {
  const access = cloneResourceAccess(draftResourceAccess.value)
  const modelAllowed = new Set(access.models.allowed)
  for (const type of MODEL_TYPES) {
    if (access.models.mode !== 'selected') {
      access.models.defaults[type] = access.models.mode === 'all' ? access.models.defaults[type] : null
      continue
    }
    if (!modelAllowed.has(access.models.defaults[type])) {
      access.models.defaults[type] = null
    }
  }
  if (access.models.mode !== 'selected') access.models.allowed = []
  if (access.tools.mode !== 'selected') access.tools.allowed = []
  if (access.mcp_servers.mode !== 'selected') access.mcp_servers.allowed = []
  return access
}

const validateDraft = () => {
  if (!selectedRole.value?.is_system && !draftName.value.trim()) return '角色名称不能为空'
  if (draftResourceAccess.value.models.mode === 'selected' && missingModelDefaultTypes.value.length) {
    return `请为以下模型类型选择默认模型：${missingModelDefaultLabels.value}`
  }
  return null
}

const saveRole = async () => {
  if (!selectedRole.value?.editable) return
  const validationError = validateDraft()
  if (validationError) {
    message.error(validationError)
    return
  }
  saving.value = true
  try {
    const payload = {
      permissions: draftPermissions.value,
      description: draftDescription.value.trim() || null,
      resource_access: buildResourceAccessPayload()
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
      description: createForm.description.trim() || null,
      permissions: [],
      resource_access: createEmptyResourceAccess()
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
  .role-editor,
  .resource-group-header,
  .model-section-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
  }

  .section-title,
  .role-editor-title,
  .group-heading,
  .model-section-title {
    color: var(--gray-900);
    font-size: 18px;
    font-weight: 600;
  }

  .role-key,
  .permission-key,
  .entry-key,
  .group-subtitle,
  .model-section-meta {
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

  .role-name {
    font-weight: 500;
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

  .editor-segment {
    margin-top: 16px;
  }

  .matrix-groups,
  .resource-groups {
    display: grid;
    gap: 16px;
    margin-top: 16px;
  }

  .matrix-groups {
    gap: 0;
    border: 1px solid var(--gray-200);
    border-radius: 8px;
    overflow: hidden;
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

  .permission-item,
  .resource-entry {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
    padding: 10px 12px;
  }

  .permission-item {
    min-height: 40px;
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

  .permission-key,
  .entry-key {
    margin-left: auto;
    overflow: hidden;
    font-size: 11px;
    font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .resource-group,
  .model-section,
  .provider-section {
    display: grid;
    gap: 12px;
  }

  .resource-group {
    padding: 16px;
    border: 1px solid var(--gray-200);
    border-radius: 8px;
  }

  .resource-toolbar {
    display: flex;
    justify-content: flex-end;
  }

  .resource-entry-list {
    display: grid;
    border: 1px solid var(--gray-150);
    border-radius: 8px;
    overflow: hidden;
  }

  .resource-entry {
    min-height: 64px;
    border-bottom: 1px solid var(--gray-150);

    &:last-child {
      border-bottom: 0;
    }

    &.disabled {
      background: var(--gray-25);
    }
  }

  .entry-main {
    display: grid;
    gap: 4px;
    min-width: 0;
  }

  .entry-title-row {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
  }

  .entry-title,
  .provider-title {
    color: var(--gray-900);
    font-weight: 600;
  }

  .entry-description {
    color: var(--gray-700);
    font-size: 12px;
    line-height: 1.5;
    word-break: break-all;
  }

  .entry-tag {
    display: inline-flex;
    align-items: center;
    padding: 2px 8px;
    border-radius: 999px;
    font-size: 12px;

    &.warning {
      background: var(--color-warning-50);
      color: var(--color-warning-700);
    }

    &.success {
      background: var(--color-success-50);
      color: var(--color-success-700);
    }
  }

  .provider-sections {
    display: grid;
    gap: 12px;
  }

  .provider-section {
    gap: 8px;
  }

  .default-model-select {
    width: 320px;
    max-width: 100%;
  }

  .offline-section {
    display: grid;
    gap: 8px;
  }

  .offline-title {
    color: var(--gray-700);
    font-size: 12px;
  }

  .offline-tags {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }

  @media (max-width: 960px) {
    .permission-layout,
    .role-fields :deep(.ant-form),
    .matrix-group,
    .permission-grid {
      grid-template-columns: 1fr;
    }

    .role-editor,
    .resource-group-header,
    .model-section-header {
      align-items: flex-start;
      flex-direction: column;
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

    .resource-entry {
      align-items: flex-start;
      flex-direction: column;
    }

    .entry-key {
      margin-left: 32px;
    }
  }
}
</style>
