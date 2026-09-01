<template>
  <div class="user-management">
    <!-- 头部区域 -->
    <div class="header-section">
      <div class="header-content">
        <div class="section-title">用户管理</div>
        <p class="section-description">
          禁用会保留账号和历史数据，可随时重新激活；删除会永久移除账号，请谨慎操作。
        </p>
      </div>
      <div class="header-actions">
        <a-button
          @click="handleRefresh"
          :loading="userManagement.refreshing"
          title="刷新"
          class="refresh-btn lucide-icon-btn"
        >
          <template #icon>
            <RefreshCw :size="16" :class="{ spin: userManagement.refreshing }" />
          </template>
        </a-button>
        <a-button
          v-if="userStore.hasPermission('users.create')"
          type="primary"
          @click="showAddUserModal"
          class="add-btn lucide-icon-btn"
        >
          <template #icon><Plus :size="16" /></template>
          添加用户
        </a-button>
      </div>
    </div>

    <div class="filter-section">
      <a-input
        v-model:value="userManagement.searchKeyword"
        class="search-input"
        placeholder="搜索用户名 / ID"
        allow-clear
      >
        <template #prefix><Search :size="16" /></template>
      </a-input>
      <div class="filter-actions">
        <div v-if="hasDepartmentFilterPermission" class="department-filter-group">
          <a-tree-select
            v-model:value="userManagement.departmentFilters"
            class="department-filter-tree"
            :tree-data="departmentTreeData"
            multiple
            tree-checkable
            tree-check-strictly
            allow-clear
            show-search
            tree-default-expand-all
            max-tag-count="responsive"
            placeholder="请选择部门（可多选）"
            :filter-tree-node="filterDepartmentTreeNode"
          />
          <div class="department-scope-switch" role="group" aria-label="部门筛选范围">
            <a-button
              size="small"
              :type="userManagement.includeDepartmentDescendants ? 'primary' : 'default'"
              @click="userManagement.includeDepartmentDescendants = true"
            >
              包含下级
            </a-button>
            <a-button
              size="small"
              :type="!userManagement.includeDepartmentDescendants ? 'primary' : 'default'"
              @click="userManagement.includeDepartmentDescendants = false"
            >
              仅直属
            </a-button>
          </div>
        </div>
        <a-select v-model:value="userManagement.roleFilter" class="filter-select">
          <a-select-option value="">全部权限</a-select-option>
          <a-select-option v-for="role in roleOptions" :key="role.key" :value="role.key">
            {{ role.name }}
          </a-select-option>
        </a-select>
        <div class="view-switch" role="group" aria-label="用户视图">
          <a-button
            size="small"
            :type="userManagement.viewMode === 'list' ? 'primary' : 'default'"
            :aria-pressed="userManagement.viewMode === 'list'"
            class="lucide-icon-btn"
            @click="userManagement.viewMode = 'list'"
          >
            <template #icon><ListIcon :size="14" /></template>
            列表
          </a-button>
          <a-button
            size="small"
            :type="userManagement.viewMode === 'card' ? 'primary' : 'default'"
            :aria-pressed="userManagement.viewMode === 'card'"
            class="lucide-icon-btn"
            @click="userManagement.viewMode = 'card'"
          >
            <template #icon><LayoutGrid :size="14" /></template>
            卡片
          </a-button>
        </div>
      </div>
    </div>

    <div
      v-if="canBatchReset || canResetQuotaGlobally"
      class="batch-action-bar"
      :class="{ 'batch-action-bar-active': selectedUserCount > 0 }"
    >
      <div class="batch-action-summary">
        <span v-if="selectedUserCount > 0">已选 {{ selectedUserCount }} 名启用用户</span>
        <span v-else-if="canBatchReset">可按筛选结果跨分页选择用户后批量重置本周额度</span>
        <span v-else>全局重置会覆盖当前权限范围内的全部启用用户</span>
      </div>
      <div class="batch-action-buttons">
        <a-button v-if="selectedUserCount > 0" size="small" @click="clearSelectedUsers">
          清空选择
        </a-button>
        <a-button
          v-if="canBatchReset"
          type="primary"
          size="small"
          :disabled="selectedUserCount === 0"
          :loading="userManagement.batchResettingQuota"
          @click="confirmResetSelectedUsers"
        >
          重置所选用户额度
        </a-button>
        <a-button
          v-if="canResetQuotaGlobally"
          danger
          size="small"
          :loading="userManagement.globalResettingQuota"
          @click="openGlobalResetModal"
        >
          全局重置额度
        </a-button>
      </div>
    </div>

    <!-- 主内容区域 -->
    <div class="content-section">
      <a-spin :spinning="userManagement.loading">
        <div v-if="userManagement.error" class="error-message">
          <a-alert type="error" :message="userManagement.error" show-icon />
        </div>

        <div class="cards-container">
          <div v-if="filteredUsers.length === 0" class="empty-state">
            <a-empty
              :description="userManagement.users.length === 0 ? '暂无用户数据' : '没有匹配的用户'"
            />
          </div>
          <div v-else-if="userManagement.viewMode === 'list'" class="user-table-wrapper">
            <a-table
              :columns="userTableColumns"
              :data-source="filteredUsers"
              :pagination="userTablePagination"
              :row-selection="tableRowSelection"
              :row-class-name="(user) => (user.is_disabled ? 'disabled-user-row' : '')"
              row-key="id"
              size="middle"
              :scroll="{ x: 1190 }"
              @change="handleUserTableChange"
            >
              <template #bodyCell="{ column, record: user }">
                <template v-if="column.key === 'actions'">
                  <div class="table-actions">
                    <a-button
                      v-if="userStore.hasPermission('users.update') && !user.is_disabled"
                      type="link"
                      size="small"
                      class="table-action-btn lucide-icon-btn"
                      @click="showEditUserModal(user)"
                    >
                      <template #icon><SquarePen :size="13" /></template>
                      编辑
                    </a-button>
                    <a-button
                      v-if="userStore.hasPermission('users.enable') && user.is_disabled"
                      type="link"
                      size="small"
                      class="table-action-btn table-action-activate lucide-icon-btn"
                      :disabled="isUserLifecycleActionDisabled(user)"
                      @click="confirmActivateUser(user)"
                    >
                      <template #icon><UserCheck :size="13" /></template>
                      激活
                    </a-button>
                    <a-button
                      v-else-if="userStore.hasPermission('users.disable') && !user.is_disabled"
                      type="link"
                      size="small"
                      class="table-action-btn lucide-icon-btn"
                      :disabled="isUserLifecycleActionDisabled(user)"
                      @click="confirmDisableUser(user)"
                    >
                      <template #icon><UserX :size="13" /></template>
                      禁用
                    </a-button>
                    <a-button
                      v-if="userStore.hasPermission('users.delete')"
                      type="link"
                      size="small"
                      danger
                      class="table-action-btn lucide-icon-btn"
                      :disabled="isUserLifecycleActionDisabled(user)"
                      @click="confirmDeleteUser(user)"
                    >
                      <template #icon><Trash2 :size="13" /></template>
                      删除
                    </a-button>
                  </div>
                </template>

                <template v-else-if="column.key === 'user'">
                  <div class="table-user">
                    <FallbackAvatar
                      :src="user.avatar"
                      :default-src="getUserDefaultAvatarSrc(user)"
                      :name="user.username"
                      :seed="user.uid || user.username"
                      kind="user"
                      :size="34"
                      shape="circle"
                      :alt="user.username"
                      class="table-avatar"
                    />
                    <div class="table-user-copy">
                      <span class="table-user-name">{{ user.username }}</span>
                      <span class="table-user-id">ID: {{ user.uid || '-' }}</span>
                    </div>
                  </div>
                </template>

                <template v-else-if="column.key === 'department'">
                  <div class="table-department">
                    <span>{{ user.department_path || user.department_name || '未分配部门' }}</span>
                    <span class="table-secondary"> 兼职：{{ partTimeLabel(user) }} </span>
                  </div>
                </template>

                <template v-else-if="column.key === 'role'">
                  <span>{{ user.role_name || roleName(user.role) }}</span>
                </template>

                <template v-else-if="column.key === 'weeklyQuota'">
                  <div class="table-weekly-quota">
                    <div class="table-weekly-line">
                      <span class="table-weekly-primary">{{ formatWeeklyQuotaPrimary(user) }}</span>
                      <span
                        v-if="
                          getUserTokenQuotaMode(user) !== 'unlimited' &&
                          getUserEffectiveWeeklyQuota(user)
                        "
                        class="table-weekly-percent"
                      >
                        {{ weeklyQuotaUsedPercent(user) }}%
                      </span>
                    </div>
                    <a-progress
                      v-if="
                        getUserTokenQuotaMode(user) !== 'unlimited' &&
                        getUserEffectiveWeeklyQuota(user)
                      "
                      class="table-weekly-progress"
                      :percent="weeklyQuotaUsedPercent(user)"
                      :stroke-color="weeklyQuotaProgressColor(user)"
                      :show-info="false"
                      :stroke-width="3"
                    />
                    <span
                      v-else-if="getUserTokenQuotaMode(user) === 'unlimited'"
                      class="table-secondary table-weekly-unlimited"
                    >
                      不限
                    </span>
                  </div>
                </template>

                <template v-else-if="column.key === 'lastLogin'">
                  <span class="table-time">{{ formatTime(user.last_login) }}</span>
                </template>

                <template v-else-if="column.key === 'createdAt'">
                  <span class="table-time">{{ formatTime(user.created_at) }}</span>
                </template>

                <template v-else-if="column.key === 'status'">
                  <span
                    class="user-status"
                    :class="user.is_disabled ? 'user-status-disabled' : 'user-status-active'"
                  >
                    {{ user.is_disabled ? '已禁用' : '正常' }}
                  </span>
                </template>
              </template>
            </a-table>
          </div>
          <div v-else>
            <div class="user-cards-grid">
              <div
                v-for="user in paginatedUsers"
                :key="user.id"
                class="user-card-shell"
                :class="{ 'user-card-shell-disabled': user.is_disabled }"
              >
                <a-checkbox
                  v-if="canBatchReset"
                  class="user-card-checkbox"
                  :checked="isUserSelected(user.id)"
                  :disabled="!canSelectUser(user)"
                  @change="toggleUserSelection(user, $event.target.checked)"
                />
                <InfoCard
                  :title="user.username"
                  :subtitle="`ID: ${user.uid || '-'}`"
                  class="user-card"
                  :class="{ 'user-card-disabled': user.is_disabled }"
                >
                  <template #icon>
                    <FallbackAvatar
                      :src="user.avatar"
                      :default-src="getUserDefaultAvatarSrc(user)"
                      :name="user.username"
                      :seed="user.uid || user.username"
                      kind="user"
                      :size="40"
                      shape="circle"
                      :alt="user.username"
                      class="avatar-img"
                    />
                  </template>

                  <template #status>
                    <div class="role-dept-badge">
                      <span class="role-icon-wrapper" :class="getRoleClass(user.role)">
                        <UserLock v-if="user.role === 'superadmin'" :size="14" />
                        <UserStar v-else-if="user.role === 'admin'" :size="14" />
                        <User v-else :size="14" />
                      </span>
                      <span class="dept-text">
                        {{ user.role_name || roleName(user.role) }}
                        <template v-if="user.department_path">
                          · {{ user.department_path }}</template
                        >
                        <template v-else-if="user.department_name">
                          · {{ user.department_name }}</template
                        >
                        <template v-if="user.is_disabled"> · 已禁用</template>
                      </span>
                    </div>
                  </template>

                  <template #card-more-action-corner>
                    <a-menu>
                      <a-menu-item
                        v-if="userStore.hasPermission('users.update') && !user.is_disabled"
                        key="edit"
                        @click.stop="showEditUserModal(user)"
                      >
                        <span class="lucide-menu-item">
                          <SquarePen :size="14" />
                          <span>编辑用户</span>
                        </span>
                      </a-menu-item>
                      <a-menu-item
                        v-if="userStore.hasPermission('users.enable') && user.is_disabled"
                        key="activate"
                        :disabled="isUserLifecycleActionDisabled(user)"
                        @click.stop="confirmActivateUser(user)"
                      >
                        <span class="lucide-menu-item">
                          <UserCheck :size="14" />
                          <span>激活用户</span>
                        </span>
                      </a-menu-item>
                      <a-menu-item
                        v-if="userStore.hasPermission('users.disable') && !user.is_disabled"
                        key="disable"
                        :disabled="isUserLifecycleActionDisabled(user)"
                        @click.stop="confirmDisableUser(user)"
                      >
                        <span class="lucide-menu-item">
                          <UserX :size="14" />
                          <span>禁用用户</span>
                        </span>
                      </a-menu-item>
                      <a-menu-item
                        v-if="userStore.hasPermission('users.delete')"
                        key="delete"
                        :disabled="isUserLifecycleActionDisabled(user)"
                        :danger="!isUserLifecycleActionDisabled(user)"
                        @click.stop="confirmDeleteUser(user)"
                      >
                        <span class="lucide-menu-item">
                          <Trash2 :size="14" />
                          <span>删除用户</span>
                        </span>
                      </a-menu-item>
                    </a-menu>
                  </template>

                  <template #info>
                    <div class="card-content">
                      <div class="info-item">
                        <span class="info-label">兼职部门:</span>
                        <span class="info-value part-time-text">
                          {{ partTimeLabel(user) }}
                        </span>
                      </div>
                      <div class="info-item">
                        <span class="info-label">Token 周额度:</span>
                        <span class="info-value quota-text">{{
                          formatWeeklyQuotaPrimary(user)
                        }}</span>
                      </div>
                      <div
                        v-if="
                          getUserTokenQuotaMode(user) !== 'unlimited' &&
                          getUserEffectiveWeeklyQuota(user)
                        "
                        class="info-item quota-progress-item"
                      >
                        <a-progress
                          class="quota-progress-bar"
                          :percent="weeklyQuotaUsedPercent(user)"
                          :stroke-color="weeklyQuotaProgressColor(user)"
                          :show-info="false"
                          :stroke-width="6"
                        />
                        <span class="quota-progress-text">
                          已用 {{ formatTokenCount(getUserWeeklyUsage(user)) }} /
                          {{ formatTokenCount(getUserEffectiveWeeklyQuota(user)) }}
                          ({{ weeklyQuotaUsedPercent(user) }}%)
                        </span>
                      </div>
                      <div class="info-item">
                        <span class="info-label">手机号:</span>
                        <span class="info-value phone-text">{{ user.phone_number || '-' }}</span>
                      </div>
                      <div class="info-item">
                        <span class="info-label">创建时间:</span>
                        <span class="info-value time-text">{{ formatTime(user.created_at) }}</span>
                      </div>
                      <div class="info-item">
                        <span class="info-label">最后登录:</span>
                        <span class="info-value time-text">{{ formatTime(user.last_login) }}</span>
                      </div>
                    </div>
                  </template>
                </InfoCard>
              </div>
            </div>
            <div v-if="filteredUsers.length > userManagement.pageSize" class="pagination-section">
              <a-pagination
                v-model:current="userManagement.currentPage"
                v-model:page-size="userManagement.pageSize"
                :total="filteredUsers.length"
                :page-size-options="['20', '50', '100']"
                show-size-changer
                size="small"
              />
            </div>
          </div>
        </div>
      </a-spin>
    </div>

    <!-- 用户表单模态框 -->
    <a-modal
      v-model:open="userManagement.modalVisible"
      :title="userManagement.modalTitle"
      @ok="handleUserFormSubmit"
      :confirmLoading="userManagement.loading"
      @cancel="userManagement.modalVisible = false"
      :maskClosable="false"
      width="480px"
      class="user-modal"
    >
      <a-form layout="vertical" class="user-form">
        <a-form-item label="用户名" required class="form-item">
          <a-input
            v-model:value="userManagement.form.username"
            placeholder="请输入用户名（2-20个字符）"
            @blur="validateAndGenerateUid"
            :maxlength="20"
          />
          <div v-if="userManagement.form.usernameError" class="error-text">
            {{ userManagement.form.usernameError }}
          </div>
          <div
            v-if="userManagement.form.generatedUid && !userManagement.editMode"
            class="help-text"
          >
            登录ID：{{ userManagement.form.generatedUid }}，此ID将用于登录，根据用户名自动生成
          </div>
        </a-form-item>

        <!-- 手机号为预留字段，仅保留已有用户的编辑能力 -->
        <a-form-item v-if="userManagement.editMode" label="手机号" class="form-item">
          <a-input
            v-model:value="userManagement.form.phoneNumber"
            placeholder="请输入手机号（可选，可用于登录）"
            :maxlength="11"
          />
          <div v-if="userManagement.form.phoneError" class="error-text">
            {{ userManagement.form.phoneError }}
          </div>
        </a-form-item>

        <template v-if="userManagement.editMode">
          <div class="password-toggle">
            <a-checkbox v-model:checked="userManagement.displayPasswordFields">
              修改密码
            </a-checkbox>
          </div>
        </template>

        <template v-if="!userManagement.editMode || userManagement.displayPasswordFields">
          <a-form-item label="密码" required class="form-item">
            <a-input-password
              v-model:value="userManagement.form.password"
              :placeholder="`请输入密码（至少 ${MIN_PASSWORD_LENGTH} 位）`"
              :minlength="MIN_PASSWORD_LENGTH"
            />
          </a-form-item>

          <a-form-item label="确认密码" required class="form-item">
            <a-input-password
              v-model:value="userManagement.form.confirmPassword"
              placeholder="请再次输入密码"
            />
          </a-form-item>
        </template>

        <a-form-item label="角色" class="form-item">
          <a-select v-if="userStore.isSuperAdmin" v-model:value="userManagement.form.role">
            <a-select-option v-for="role in roles" :key="role.key" :value="role.key">
              {{ role.name }}
            </a-select-option>
          </a-select>
          <a-input v-else :value="roleName(userManagement.form.role)" disabled />
          <div v-if="!userStore.isSuperAdmin" class="help-text">只有超级管理员可以修改角色</div>
        </a-form-item>

        <a-form-item label="周额度" class="form-item">
          <div class="quota-form-group">
            <a-select v-model:value="userManagement.form.tokenQuotaMode">
              <a-select-option
                v-for="option in tokenQuotaModeOptions"
                :key="option.value"
                :value="option.value"
              >
                {{ option.label }}
              </a-select-option>
            </a-select>
            <a-input-number
              v-if="userManagement.form.tokenQuotaMode === 'custom'"
              v-model:value="userManagement.form.weeklyTokenQuota"
              class="quota-input"
              :min="0"
              :step="10000"
              :precision="0"
              placeholder="请输入 Token 数"
            />
            <a-button
              v-if="userManagement.editMode && userStore.hasPermission('users.quota.manage')"
              :loading="userManagement.resettingQuota"
              @click="confirmResetWeeklyQuota"
            >
              重置本周已用额度
            </a-button>
          </div>
          <div
            v-if="userManagement.editMode && userStore.hasPermission('users.quota.manage')"
            class="help-text"
          >
            仅清零本周已用量，不修改额度模式和额度上限。
          </div>
        </a-form-item>

        <template v-if="userStore.isSuperAdmin">
          <a-form-item label="主部门" required class="form-item">
            <a-select
              v-model:value="userManagement.form.primaryDepartmentId"
              show-search
              option-filter-prop="label"
              placeholder="请选择唯一主部门"
              @change="handlePrimaryDepartmentChange"
            >
              <a-select-option
                v-for="dept in activeDepartments"
                :key="dept.id"
                :value="dept.id"
                :label="dept.path_label"
              >
                {{ dept.path_label }}
              </a-select-option>
            </a-select>
          </a-form-item>

          <a-form-item label="兼职部门" class="form-item">
            <a-select
              v-model:value="userManagement.form.partTimeDepartmentIds"
              mode="multiple"
              show-search
              option-filter-prop="label"
              :disabled="partTimeDepartmentOptions.length === 0"
              placeholder="可选择同一主体下的兼职部门"
            >
              <a-select-option
                v-for="dept in partTimeDepartmentOptions"
                :key="dept.id"
                :value="dept.id"
                :label="dept.path_label"
              >
                {{ dept.path_label }}
              </a-select-option>
            </a-select>
            <div class="help-text">
              兼职部门必须与主部门属于同一主体；默认部门用户不能设置兼职。
            </div>
          </a-form-item>

          <a-form-item label="可管理部门" class="form-item">
            <a-select
              v-model:value="userManagement.form.managedDepartmentIds"
              mode="multiple"
              show-search
              option-filter-prop="label"
              placeholder="留空表示不授予组织管理范围"
            >
              <a-select-option
                v-for="dept in activeDepartments"
                :key="dept.id"
                :value="dept.id"
                :label="dept.path_label"
              >
                {{ dept.path_label }}（含下级）
              </a-select-option>
            </a-select>
            <div class="help-text">所选节点会覆盖整棵子树；员工归属不会自动授予管理权限。</div>
          </a-form-item>
        </template>
      </a-form>
    </a-modal>

    <a-modal
      v-model:open="userManagement.globalResetModalVisible"
      title="全局重置额度"
      :confirm-loading="userManagement.globalResettingQuota"
      :ok-button-props="{
        danger: true,
        disabled:
          userManagement.globalResetConfirmText.trim() !== GLOBAL_RESET_CONFIRM_TEXT ||
          userManagement.globalResettingQuota
      }"
      ok-text="确认重置"
      cancel-text="取消"
      @ok="handleGlobalReset"
      @cancel="closeGlobalResetModal"
    >
      <div class="global-reset-modal">
        <p class="global-reset-summary">
          将重置{{ globalResetScopeLabel }}，共 {{ managementScopeUserCount }} 名启用用户。
        </p>
        <p class="global-reset-hint">
          仅清零本周有效用量，不修改额度模式、额度上限或历史消费明细。
        </p>
        <a-input
          v-model:value="userManagement.globalResetConfirmText"
          :placeholder="`请输入“${GLOBAL_RESET_CONFIRM_TEXT}”确认`"
        />
      </div>
    </a-modal>
  </div>
</template>

<script setup>
import { reactive, ref, onMounted, watch, computed } from 'vue'
import { message, Modal } from 'ant-design-vue'
import { useUserStore } from '@/stores/user'
import { departmentApi } from '@/apis'
import { roleApi } from '@/apis/role_api'
import {
  Plus,
  SquarePen,
  Trash2,
  UserCheck,
  UserX,
  LayoutGrid,
  List as ListIcon,
  User,
  UserLock,
  UserStar,
  RefreshCw,
  Search
} from 'lucide-vue-next'
import { formatDateTime } from '@/utils/time'
import { isPasswordLongEnough, MIN_PASSWORD_LENGTH } from '@/utils/passwordValidation'
import { generatePixelAvatar } from '@/utils/pixelAvatar'
import {
  buildDepartmentTreeSelectData,
  expandDepartmentFilterIds,
  filterUserManagementUsers
} from '@/utils/userManagementFilters'
import FallbackAvatar from '@/components/common/FallbackAvatar.vue'
import InfoCard from '@/components/shared/InfoCard.vue'

const userStore = useUserStore()
const roles = ref([])
const GLOBAL_RESET_CONFIRM_TEXT = '重置额度'
const tokenQuotaModeOptions = [
  { value: 'inherit', label: '继承' },
  { value: 'custom', label: '自定义' },
  { value: 'unlimited', label: '不限' }
]

const roleName = (key) => {
  const role = roles.value.find((item) => item.key === key)
  if (role) return role.name
  return { superadmin: '超级管理员', admin: '管理员', user: '普通用户' }[key] || key
}

const getQuotaRecord = (user) =>
  user?.token_quota && typeof user.token_quota === 'object' && !Array.isArray(user.token_quota)
    ? user.token_quota
    : {}

const normalizeWholeNumber = (value) => {
  if (value === null || typeof value === 'undefined' || value === '') return null
  const parsed = Number(value)
  if (!Number.isFinite(parsed) || parsed < 0) return null
  return Math.round(parsed)
}

const normalizeTokenQuotaMode = (value) =>
  ['inherit', 'custom', 'unlimited'].includes(value) ? value : 'inherit'

const getUserTokenQuotaMode = (user) => normalizeTokenQuotaMode(getQuotaRecord(user).mode)

const getUserCustomWeeklyQuota = (user) => {
  const quotaRecord = getQuotaRecord(user)
  return normalizeWholeNumber(quotaRecord.configured_quota ?? quotaRecord.effective_quota)
}

const getUserEffectiveWeeklyQuota = (user) =>
  normalizeWholeNumber(getQuotaRecord(user).effective_quota)

const getUserWeeklyUsage = (user) => normalizeWholeNumber(getQuotaRecord(user).used)

const formatTokenCount = (value) => {
  if (!Number.isFinite(value)) return '-'
  return new Intl.NumberFormat('zh-CN').format(value)
}

const formatWeeklyQuotaPrimary = (user) => {
  const mode = getUserTokenQuotaMode(user)
  if (mode === 'unlimited') return '不限'
  const quota = getUserEffectiveWeeklyQuota(user)
  if (quota !== null) return formatTokenCount(quota)
  return '-'
}

const weeklyQuotaUsedPercent = (user) => {
  const quota = getUserEffectiveWeeklyQuota(user)
  const used = getUserWeeklyUsage(user)
  if (!Number.isFinite(quota) || quota <= 0 || !Number.isFinite(used)) return 0
  const percent = (used / quota) * 100
  return Math.max(0, Math.min(100, Math.round(percent)))
}

const weeklyQuotaProgressColor = (user) => {
  const percent = weeklyQuotaUsedPercent(user)
  if (percent >= 90) return 'var(--color-error-500)'
  if (percent >= 70) return 'var(--color-warning-500)'
  return 'var(--main-500)'
}

const canBatchReset = computed(() => userStore.hasPermission('users.quota.manage'))
const canResetQuotaGlobally = computed(() => userStore.hasPermission('users.quota.reset.global'))
const hasDepartmentFilterPermission = computed(() => userStore.hasPermission('departments.read'))

// 用户管理相关状态
const userManagement = reactive({
  loading: false,
  resettingQuota: false,
  batchResettingQuota: false,
  globalResettingQuota: false,
  refreshing: false,
  users: [],
  searchKeyword: '',
  departmentFilters: [],
  includeDepartmentDescendants: true,
  roleFilter: '',
  viewMode: 'list',
  currentPage: 1,
  pageSize: 50,
  error: null,
  modalVisible: false,
  modalTitle: '添加用户',
  editMode: false,
  editUserId: null,
  selectedUserIds: [],
  globalResetModalVisible: false,
  globalResetConfirmText: '',
  form: {
    username: '',
    generatedUid: '', // 自动生成的uid
    phoneNumber: '', // 手机号
    password: '',
    confirmPassword: '',
    role: 'user', // 默认角色
    tokenQuotaMode: 'inherit',
    weeklyTokenQuota: null,
    primaryDepartmentId: null,
    partTimeDepartmentIds: [],
    managedDepartmentIds: [],
    usernameError: '', // 用户名错误信息
    phoneError: '' // 手机号错误信息
  },
  displayPasswordFields: true // 编辑时是否显示密码字段
})

const roleOptions = computed(() => {
  if (roles.value.length) return roles.value
  const keys = [...new Set(userManagement.users.map((user) => user.role))]
  return keys.map((key) => ({ key, name: roleName(key) }))
})

const userTableColumns = [
  { title: '操作', key: 'actions', width: 190, fixed: 'left' },
  { title: '用户', key: 'user', width: 205 },
  { title: '部门', key: 'department', width: 165 },
  { title: '角色', key: 'role', width: 100 },
  { title: '本周额度', key: 'weeklyQuota', width: 180 },
  { title: '状态', key: 'status', width: 80 },
  { title: '最后登录时间', key: 'lastLogin', width: 135 },
  { title: '创建时间', key: 'createdAt', width: 135 }
]

// 部门列表（仅超级管理员使用）
const departmentManagement = reactive({
  departments: [],
  tree: []
})

const activeDepartments = computed(() =>
  departmentManagement.departments.filter((dept) => dept.status === 'active')
)

const selectedPrimaryDepartment = computed(() =>
  departmentManagement.departments.find(
    (dept) => dept.id === userManagement.form.primaryDepartmentId
  )
)

const partTimeDepartmentOptions = computed(() => {
  const primary = selectedPrimaryDepartment.value
  if (!primary || primary.is_system) return []
  return activeDepartments.value.filter(
    (dept) => dept.root_id === primary.root_id && dept.id !== primary.id
  )
})

const departmentTreeData = computed(() => buildDepartmentTreeSelectData(departmentManagement.tree))
const effectiveDepartmentFilterIds = computed(() =>
  expandDepartmentFilterIds(
    userManagement.departmentFilters,
    departmentManagement.tree,
    userManagement.includeDepartmentDescendants
  )
)

const filteredUsers = computed(() => {
  return filterUserManagementUsers(userManagement.users, {
    keyword: userManagement.searchKeyword,
    role: userManagement.roleFilter,
    departmentIds: effectiveDepartmentFilterIds.value
  })
})

const paginatedUsers = computed(() => {
  const pageSize = Number(userManagement.pageSize)
  const start = (userManagement.currentPage - 1) * pageSize
  return filteredUsers.value.slice(start, start + pageSize)
})

const selectedUsers = computed(() => {
  const usersById = new Map(userManagement.users.map((user) => [user.id, user]))
  return userManagement.selectedUserIds.map((userId) => usersById.get(userId)).filter(Boolean)
})

const selectedUserCount = computed(() => selectedUsers.value.length)

const managementScopeUserCount = computed(
  () => userManagement.users.filter((user) => canSelectUser(user)).length
)
const globalResetScopeLabel = computed(() =>
  userStore.isSuperAdmin ? '全平台全部启用用户' : '当前可管理组织范围内的全部启用用户'
)
const tableRowSelection = computed(() => {
  if (!canBatchReset.value) return undefined
  return {
    selectedRowKeys: userManagement.selectedUserIds,
    preserveSelectedRowKeys: true,
    columnWidth: 52,
    getCheckboxProps: (record) => ({
      disabled: !canSelectUser(record)
    }),
    onChange: (selectedRowKeys) => {
      userManagement.selectedUserIds = normalizeSelectedUserIds(selectedRowKeys)
    }
  }
})

const userTablePagination = computed(() => ({
  current: userManagement.currentPage,
  pageSize: userManagement.pageSize,
  total: filteredUsers.value.length,
  pageSizeOptions: ['20', '50', '100'],
  showSizeChanger: true,
  showTotal: (total) => `共 ${total} 名用户`
}))

const handleUserTableChange = (pager) => {
  userManagement.currentPage = pager.current
  userManagement.pageSize = pager.pageSize
}

// 获取部门列表
const fetchDepartments = async () => {
  if (!hasDepartmentFilterPermission.value) {
    departmentManagement.departments = []
    departmentManagement.tree = []
    userManagement.departmentFilters = []
    return
  }

  try {
    const [departments, tree] = await Promise.all([
      departmentApi.getDepartments(),
      departmentApi.getDepartmentTree()
    ])
    departmentManagement.departments = departments
    departmentManagement.tree = Array.isArray(tree) ? tree : []
  } catch (error) {
    console.error('获取部门列表失败:', error)
  }
}

const fetchRoles = async () => {
  if (!userStore.isSuperAdmin) return
  try {
    const result = await roleApi.getRoles()
    roles.value = result.roles || []
  } catch (error) {
    console.error('获取角色列表失败:', error)
  }
}

// 添加验证用户名并生成uid的函数
const validateAndGenerateUid = async () => {
  const username = userManagement.form.username.trim()

  // 清空之前的错误和生成的ID
  userManagement.form.usernameError = ''
  userManagement.form.generatedUid = ''

  if (!username) {
    return
  }

  // 在编辑模式下，不需要重新生成uid
  if (userManagement.editMode) {
    return
  }

  try {
    const result = await userStore.validateUsernameAndGenerateUid(username)
    userManagement.form.generatedUid = result.uid
  } catch (error) {
    userManagement.form.usernameError = error.message || '用户名验证失败'
  }
}

// 验证手机号格式
const validatePhoneNumber = (phone) => {
  if (!phone) {
    return true // 手机号可选
  }

  // 中国大陆手机号格式验证
  const phoneRegex = /^1[3-9]\d{9}$/
  return phoneRegex.test(phone)
}

// 监听密码字段显示状态变化
watch(
  () => userManagement.displayPasswordFields,
  (newVal) => {
    // 当取消显示密码字段时，清空密码输入
    if (!newVal) {
      userManagement.form.password = ''
      userManagement.form.confirmPassword = ''
    }
  }
)

// 监听手机号输入变化
watch(
  () => userManagement.form.phoneNumber,
  (newPhone) => {
    userManagement.form.phoneError = ''

    if (newPhone && !validatePhoneNumber(newPhone)) {
      userManagement.form.phoneError = '请输入正确的手机号格式'
    }
  }
)

watch(
  () => [
    userManagement.searchKeyword,
    userManagement.departmentFilters,
    userManagement.includeDepartmentDescendants,
    userManagement.roleFilter
  ],
  () => {
    userManagement.currentPage = 1
  }
)

watch(
  () => filteredUsers.value.length,
  (total) => {
    const maxPage = Math.max(1, Math.ceil(total / Number(userManagement.pageSize)))
    if (userManagement.currentPage > maxPage) {
      userManagement.currentPage = maxPage
    }
  }
)

// 格式化时间显示
const formatTime = (timeStr) => formatDateTime(timeStr)

const getUserDefaultAvatarSrc = (user) => (user.uid ? generatePixelAvatar(user.uid) : '')

const partTimeLabel = (user) => {
  const memberships = user.part_time_departments || []
  if (!memberships.length) return '-'
  return memberships.map((item) => item.path_label || item.name).join('、')
}

const normalizeSelectedUserIds = (userIds) => [
  ...new Set(userIds.map((userId) => Number(userId)).filter((userId) => Number.isFinite(userId)))
]

const canSelectUser = (user) => !user?.is_disabled

const isUserSelected = (userId) => userManagement.selectedUserIds.includes(userId)

const toggleUserSelection = (user, checked) => {
  if (!canSelectUser(user)) return

  const selectedIds = new Set(userManagement.selectedUserIds)
  if (checked) {
    selectedIds.add(user.id)
  } else {
    selectedIds.delete(user.id)
  }
  userManagement.selectedUserIds = [...selectedIds]
}

const clearSelectedUsers = () => {
  userManagement.selectedUserIds = []
}

const filterDepartmentTreeNode = (inputValue, treeNode) =>
  String(treeNode.title || '')
    .toLowerCase()
    .includes(
      String(inputValue || '')
        .trim()
        .toLowerCase()
    )

const isUserLifecycleActionDisabled = (user) =>
  user.id === userStore.userId || (userStore.userRole !== 'superadmin' && user.role !== 'user')

// 获取用户列表
const fetchUsers = async () => {
  try {
    userManagement.loading = true
    const users = await userStore.getUsers()
    userManagement.users = users
    userManagement.selectedUserIds = userManagement.selectedUserIds.filter((userId) =>
      users.some((user) => user.id === userId && canSelectUser(user))
    )
    userManagement.error = null
  } catch (error) {
    console.error('获取用户列表失败:', error)
    userManagement.error = '获取用户列表失败'
  } finally {
    userManagement.loading = false
  }
}

// 刷新用户和部门信息
const handleRefresh = async () => {
  if (userManagement.refreshing) return
  userManagement.refreshing = true
  try {
    await Promise.all([fetchUsers(), fetchDepartments(), fetchRoles()])
    message.success('刷新成功')
  } catch (error) {
    console.error('刷新失败:', error)
    message.error('刷新失败')
  } finally {
    userManagement.refreshing = false
  }
}

// 打开添加用户模态框
const showAddUserModal = () => {
  userManagement.modalTitle = '添加用户'
  userManagement.editMode = false
  userManagement.editUserId = null
  userManagement.form = {
    username: '',
    generatedUid: '',
    phoneNumber: '',
    password: '',
    confirmPassword: '',
    role: 'user', // 默认角色为普通用户
    tokenQuotaMode: 'inherit',
    weeklyTokenQuota: null,
    primaryDepartmentId: activeDepartments.value.find((dept) => dept.is_system)?.id || null,
    partTimeDepartmentIds: [],
    managedDepartmentIds: [],
    usernameError: '',
    phoneError: ''
  }
  userManagement.displayPasswordFields = true
  userManagement.modalVisible = true
}

// 打开编辑用户模态框
const showEditUserModal = async (user) => {
  userManagement.modalTitle = '编辑用户'
  userManagement.editMode = true
  userManagement.editUserId = user.id
  userManagement.form = {
    username: user.username,
    generatedUid: user.uid || '', // 编辑模式显示现有的uid
    phoneNumber: user.phone_number || '',
    password: '',
    confirmPassword: '',
    role: user.role,
    tokenQuotaMode: getUserTokenQuotaMode(user),
    weeklyTokenQuota: getUserCustomWeeklyQuota(user),
    primaryDepartmentId: user.department_id || null,
    partTimeDepartmentIds: (user.part_time_departments || []).map((item) => item.department_id),
    managedDepartmentIds: [...(user.managed_department_ids || [])],
    usernameError: '',
    phoneError: ''
  }
  userManagement.displayPasswordFields = false // 默认不显示密码字段
  userManagement.modalVisible = true
  if (userStore.isSuperAdmin) {
    try {
      const scope = await userStore.getManagedDepartments(user.id)
      userManagement.form.managedDepartmentIds = scope.department_ids || []
    } catch (error) {
      message.error(error.message || '获取管理范围失败')
    }
  }
}

const handlePrimaryDepartmentChange = () => {
  const allowed = new Set(partTimeDepartmentOptions.value.map((dept) => dept.id))
  userManagement.form.partTimeDepartmentIds = userManagement.form.partTimeDepartmentIds.filter(
    (id) => allowed.has(id)
  )
}

const confirmResetWeeklyQuota = () => {
  const target = userManagement.users.find((user) => user.id === userManagement.editUserId)
  if (!target) {
    message.error('未找到要重置额度的用户')
    return
  }

  Modal.confirm({
    title: '确认重置本周已用额度',
    content: `确定要将用户“${target.username}”本周已用 Token 额度清零吗？额度模式和上限不会改变。`,
    okText: '确认重置',
    cancelText: '取消',
    async onOk() {
      try {
        userManagement.resettingQuota = true
        await userStore.resetUserWeeklyTokenQuota(target.id)
        await fetchUsers()
        message.success('本周已用额度已重置')
      } catch (error) {
        console.error('重置用户周额度失败:', error)
        message.error(error.message || '重置失败，请稍后重试')
      } finally {
        userManagement.resettingQuota = false
      }
    }
  })
}

const confirmResetSelectedUsers = () => {
  if (!selectedUserCount.value) {
    message.warning('请先选择要重置额度的启用用户')
    return
  }

  Modal.confirm({
    title: '确认重置所选用户额度',
    content: `确定要重置这 ${selectedUserCount.value} 名用户本周已用 Token 额度吗？额度模式、额度上限和历史消费明细不会改变。`,
    okText: '确认重置',
    cancelText: '取消',
    async onOk() {
      try {
        userManagement.batchResettingQuota = true
        const summary = await userStore.resetUsersWeeklyTokenQuota(userManagement.selectedUserIds)
        await fetchUsers()
        clearSelectedUsers()
        message.success(`已重置 ${summary.reset_count}/${summary.target_count} 名用户的本周额度`)
      } catch (error) {
        console.error('批量重置用户周额度失败:', error)
        message.error(error.message || '批量重置失败，请稍后重试')
      } finally {
        userManagement.batchResettingQuota = false
      }
    }
  })
}

const openGlobalResetModal = () => {
  userManagement.globalResetConfirmText = ''
  userManagement.globalResetModalVisible = true
}

const closeGlobalResetModal = () => {
  userManagement.globalResetModalVisible = false
  userManagement.globalResetConfirmText = ''
}

const handleGlobalReset = async () => {
  if (userManagement.globalResetConfirmText.trim() !== GLOBAL_RESET_CONFIRM_TEXT) {
    message.error(`请输入“${GLOBAL_RESET_CONFIRM_TEXT}”确认`)
    return
  }

  try {
    userManagement.globalResettingQuota = true
    const summary = await userStore.resetGlobalUsersWeeklyTokenQuota()
    await fetchUsers()
    clearSelectedUsers()
    closeGlobalResetModal()
    message.success(`已重置 ${summary.reset_count}/${summary.target_count} 名用户的本周额度`)
  } catch (error) {
    console.error('全局重置用户周额度失败:', error)
    message.error(error.message || '全局重置失败，请稍后重试')
  } finally {
    userManagement.globalResettingQuota = false
  }
}

// 处理用户表单提交
const handleUserFormSubmit = async () => {
  try {
    // 简单验证
    if (!userManagement.form.username.trim()) {
      message.error('用户名不能为空')
      return
    }

    // 验证用户名长度
    if (
      userManagement.form.username.trim().length < 2 ||
      userManagement.form.username.trim().length > 20
    ) {
      message.error('用户名长度必须在 2-20 个字符之间')
      return
    }

    // 手机号仅是已有用户的预留编辑字段，不参与新建用户校验
    if (
      userManagement.editMode &&
      userManagement.form.phoneNumber &&
      !validatePhoneNumber(userManagement.form.phoneNumber)
    ) {
      message.error('请输入正确的手机号格式')
      return
    }

    if (userManagement.displayPasswordFields) {
      if (!userManagement.form.password) {
        message.error('密码不能为空')
        return
      }

      if (!isPasswordLongEnough(userManagement.form.password)) {
        message.error(`密码至少需要 ${MIN_PASSWORD_LENGTH} 个字符`)
        return
      }

      if (userManagement.form.password !== userManagement.form.confirmPassword) {
        message.error('两次输入的密码不一致')
        return
      }
    }

    if (userStore.isSuperAdmin && !userManagement.form.primaryDepartmentId) {
      message.error('请选择主部门')
      return
    }

    if (
      userManagement.form.tokenQuotaMode === 'custom' &&
      (!Number.isFinite(userManagement.form.weeklyTokenQuota) ||
        userManagement.form.weeklyTokenQuota < 0)
    ) {
      message.error('请输入有效的自定义周额度')
      return
    }

    userManagement.loading = true

    // 根据模式决定创建还是更新用户
    if (userManagement.editMode) {
      // 创建更新数据对象
      const updateData = { username: userManagement.form.username.trim() }
      if (userStore.isSuperAdmin) updateData.role = userManagement.form.role
      updateData.token_quota_mode = userManagement.form.tokenQuotaMode
      updateData.weekly_token_quota =
        userManagement.form.tokenQuotaMode === 'custom'
          ? Math.round(userManagement.form.weeklyTokenQuota)
          : null

      // 添加手机号字段
      if (userManagement.form.phoneNumber) {
        updateData.phone_number = userManagement.form.phoneNumber
      }

      if (userStore.isSuperAdmin) {
        updateData.primary_department_id = userManagement.form.primaryDepartmentId
        updateData.part_time_department_ids = userManagement.form.partTimeDepartmentIds
      }

      // 如果显示了密码字段并且填写了密码，才更新密码
      if (userManagement.displayPasswordFields && userManagement.form.password) {
        updateData.password = userManagement.form.password
      }

      await userStore.updateUser(userManagement.editUserId, updateData)
      if (userStore.isSuperAdmin) {
        await userStore.updateManagedDepartments(
          userManagement.editUserId,
          userManagement.form.managedDepartmentIds
        )
      }
      message.success('用户更新成功')
    } else {
      // 创建新用户
      const createData = {
        username: userManagement.form.username.trim(),
        password: userManagement.form.password,
        role: userManagement.form.role,
        token_quota_mode: userManagement.form.tokenQuotaMode,
        weekly_token_quota:
          userManagement.form.tokenQuotaMode === 'custom'
            ? Math.round(userManagement.form.weeklyTokenQuota)
            : null
      }

      if (userStore.isSuperAdmin) {
        createData.primary_department_id = userManagement.form.primaryDepartmentId
        createData.part_time_department_ids = userManagement.form.partTimeDepartmentIds
      }

      const createdUser = await userStore.createUser(createData)
      if (userStore.isSuperAdmin && userManagement.form.managedDepartmentIds.length) {
        await userStore.updateManagedDepartments(
          createdUser.id,
          userManagement.form.managedDepartmentIds
        )
      }
      message.success('用户创建成功')
    }

    // 重新获取用户列表
    await fetchUsers()
    userManagement.modalVisible = false
  } catch (error) {
    console.error('用户操作失败:', error)
    message.error(error.message || '操作失败，请稍后重试')
  } finally {
    userManagement.loading = false
  }
}

// 禁用用户
const confirmDisableUser = (user) => {
  if (user.id === userStore.userId) {
    message.error('不能禁用自己的账户')
    return
  }

  Modal.confirm({
    title: '确认禁用用户',
    content: `确定要禁用用户 "${user.username}" 吗？禁用后该用户将无法登录，但账号和历史数据会保留。`,
    okText: '禁用',
    cancelText: '取消',
    async onOk() {
      try {
        userManagement.loading = true
        await userStore.disableUser(user.id)
        message.success('用户已禁用')
        await fetchUsers()
      } catch (error) {
        console.error('禁用用户失败:', error)
        message.error(error.message || '禁用失败，请稍后重试')
      } finally {
        userManagement.loading = false
      }
    }
  })
}

// 重新激活用户
const confirmActivateUser = (user) => {
  Modal.confirm({
    title: '确认激活用户',
    content: `确定要重新激活用户 "${user.username}" 吗？激活后该用户可以重新登录系统。`,
    okText: '激活',
    cancelText: '取消',
    async onOk() {
      try {
        userManagement.loading = true
        await userStore.activateUser(user.id)
        message.success('用户已激活')
        await fetchUsers()
      } catch (error) {
        console.error('激活用户失败:', error)
        message.error(error.message || '激活失败，请稍后重试')
      } finally {
        userManagement.loading = false
      }
    }
  })
}

// 物理删除用户
const confirmDeleteUser = (user) => {
  // 自己不能删除自己
  if (user.id === userStore.userId) {
    message.error('不能删除自己的账户')
    return
  }

  // 确认对话框
  Modal.confirm({
    title: '确认删除用户',
    content: `确定要永久删除用户 "${user.username}" 吗？账号及其认证绑定将被物理删除，此操作不可撤销。`,
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      try {
        userManagement.loading = true
        await userStore.deleteUser(user.id)
        message.success('用户删除成功')
        // 重新获取用户列表
        await fetchUsers()
      } catch (error) {
        console.error('删除用户失败:', error)
        message.error(error.message || '删除失败，请稍后重试')
      } finally {
        userManagement.loading = false
      }
    }
  })
}

const getRoleClass = (role) => {
  switch (role) {
    case 'superadmin':
      return 'role-superadmin'
    case 'admin':
      return 'role-admin'
    case 'user':
      return 'role-user'
    default:
      return 'role-default'
  }
}

// 在组件挂载时获取用户列表
onMounted(async () => {
  await Promise.all([fetchUsers(), fetchDepartments(), fetchRoles()])
})
</script>

<style lang="less" scoped>
.user-management {
  .header-section {
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
    gap: 16px;
    margin-bottom: 16px;

    .header-content {
      flex: 1;
      min-width: 0;

      .section-title {
        font-size: 16px;
        font-weight: 500;
        color: var(--gray-900);
        line-height: 1.4;
        margin: 12px 0 12px;
      }

      .section-description {
        font-size: 14px;
        color: var(--gray-600);
        line-height: 1.4;
        margin: 0;
      }
    }

    .header-actions {
      display: flex;
      align-items: center;
      gap: 8px;

      .refresh-btn {
        display: flex;
        align-items: center;
        justify-content: center;
        width: 32px;
        height: 32px;
        border-radius: 6px;
        transition: all 0.2s ease;

        &:hover {
          background: var(--gray-25);
        }

        .spin {
          animation: spin 1s linear infinite;
        }

        :deep(.ant-btn-loading-icon) {
          color: var(--gray-600);
        }
      }
    }
  }

  .filter-section {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 16px;
    flex-wrap: wrap;

    .search-input {
      width: 300px;
      max-width: 100%;

      :deep(.ant-input-prefix) {
        color: var(--gray-500);
        margin-right: 6px;
      }
    }

    .filter-actions {
      display: flex;
      align-items: center;
      justify-content: flex-end;
      gap: 8px;
      margin-left: auto;
      flex-wrap: wrap;
    }

    .filter-select {
      width: 150px;
    }

    .department-filter-group {
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
    }

    .department-filter-tree {
      width: 320px;
      max-width: 100%;
    }

    .department-scope-switch {
      display: inline-flex;

      .ant-btn + .ant-btn {
        margin-left: -1px;
      }
    }

    .view-switch {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding-left: 4px;

      .ant-btn {
        min-width: 68px;
      }
    }
  }

  @media (max-width: 640px) {
    .filter-section {
      align-items: stretch;

      .search-input,
      .filter-actions {
        width: 100%;
      }

      .filter-actions {
        margin-left: 0;
        flex-wrap: wrap;
      }

      .filter-select {
        flex: 1;
        min-width: 0;
      }

      .department-filter-group,
      .department-filter-tree {
        width: 100%;
      }

      .view-switch {
        width: 100%;
        padding-left: 0;

        .ant-btn {
          flex: 1;
        }
      }
    }
  }

  .batch-action-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 16px;
    padding: 12px 14px;
    border: 1px solid var(--gray-150);
    border-radius: 10px;
    background: var(--gray-25);

    .batch-action-summary {
      font-size: 13px;
      color: var(--gray-700);
    }

    .batch-action-buttons {
      display: flex;
      align-items: center;
      justify-content: flex-end;
      gap: 8px;
      flex-wrap: wrap;
    }

    &.batch-action-bar-active {
      border-color: var(--main-200);
      background: var(--main-50);
    }
  }

  @media (max-width: 640px) {
    .batch-action-bar {
      align-items: stretch;
      flex-direction: column;

      .batch-action-buttons {
        width: 100%;

        .ant-btn {
          flex: 1;
        }
      }
    }
  }

  .content-section {
    overflow: hidden;

    .error-message {
      padding: 16px 24px;
    }

    .cards-container {
      .empty-state {
        padding: 60px 20px;
        text-align: center;
      }

      .user-cards-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(250px, 1fr));
        gap: 16px;
        // padding: 16px;

        .user-card-shell {
          position: relative;
        }

        .user-card-checkbox {
          position: absolute;
          top: 14px;
          left: 14px;
          z-index: 2;
          padding: 2px;
          border-radius: 6px;
          background: rgba(255, 255, 255, 0.92);
        }

        .user-card-shell-disabled {
          .user-card-checkbox {
            background: var(--gray-50);
          }
        }

        .user-card {
          cursor: default;

          :deep(.info-card-header) {
            display: grid;
            grid-template-columns: 40px minmax(0, 1fr) 28px;
            grid-template-rows: auto auto;
            column-gap: 12px;
            row-gap: 4px;
          }

          :deep(.info-card-icon) {
            grid-row: 1 / span 2;
            border-radius: 50%;
          }

          :deep(.info-card-info) {
            grid-column: 2;
            grid-row: 1;
          }

          :deep(.info-card-status) {
            grid-column: 2;
            grid-row: 2;
            min-width: 0;
            max-width: 100%;
            justify-self: start;
          }

          :deep(.card-more-action-corner) {
            grid-column: 3;
            grid-row: 1 / span 2;
          }

          :deep(.info-card-body) {
            display: flex;
            flex-direction: column;
            gap: 8px;
          }

          .avatar-img {
            width: 100%;
            height: 100%;
            object-fit: cover;
          }

          .role-dept-badge {
            display: inline-flex;
            align-items: center;
            gap: 4px;
            min-width: 0;
            max-width: 100%;
            padding: 2px 8px 2px 4px;
            background: var(--gray-50);
            border-radius: 4px;

            .role-icon-wrapper {
              display: flex;
              align-items: center;
              justify-content: center;
              width: 16px;
              height: 16px;

              &.role-superadmin {
                color: var(--color-error-700);
              }
              &.role-admin {
                color: var(--color-info-700);
              }
              &.role-user {
                color: var(--color-success-700);
              }
            }

            .dept-text {
              min-width: 0;
              font-size: 12px;
              color: var(--gray-700);
              font-weight: 500;
              overflow: hidden;
              text-overflow: ellipsis;
              white-space: nowrap;
            }
          }

          .card-content {
            .info-item {
              display: flex;
              justify-content: space-between;
              align-items: center;
              padding: 2px 0;
              border-bottom: 1px solid var(--gray-25);

              &:last-child {
                border-bottom: none;
              }

              &.quota-progress-item {
                flex-direction: column;
                align-items: stretch;
                gap: 4px;
                padding: 6px 0;
              }

              .info-label {
                font-size: 12px;
                color: var(--gray-600);
                font-weight: 500;
                min-width: 70px;
              }

              .info-value {
                font-size: 12px;
                color: var(--gray-900);
                text-align: right;
                flex: 1;

                &.time-text {
                  color: var(--gray-700);
                }

                &.phone-text {
                  font-family: 'Monaco', 'Consolas', monospace;
                }
              }

              .quota-progress-bar {
                margin: 0;
              }

              .quota-progress-text {
                color: var(--gray-500);
                font-size: 12px;
                font-variant-numeric: tabular-nums;
              }
            }
          }
        }

        .user-card-disabled {
          opacity: 0.72;
        }
      }

      .user-table-wrapper {
        overflow: hidden;
        border: 1px solid var(--gray-150);
        border-radius: 8px;
        background: var(--gray-0);

        :deep(.ant-table) {
          background: transparent;
          color: var(--gray-900);
        }

        :deep(.ant-table-thead > tr > th) {
          padding: 10px 14px;
          background: var(--gray-25);
          border-bottom-color: var(--gray-150);
          color: var(--gray-600);
          font-size: 12px;
          font-weight: 600;
        }

        :deep(.ant-table-tbody > tr > td) {
          padding: 11px 14px;
          border-bottom-color: var(--gray-100);
        }

        :deep(.ant-table-tbody > tr:last-child > td) {
          border-bottom: 0;
        }

        :deep(.ant-table-tbody > tr:hover > td) {
          background: var(--gray-25);
        }

        :deep(.ant-table-tbody > .disabled-user-row > td) {
          background: var(--gray-10);
        }

        :deep(.ant-table-tbody > .disabled-user-row:hover > td) {
          background: var(--gray-25);
        }

        .table-actions,
        .table-user {
          display: flex;
          align-items: center;
        }

        .table-actions {
          gap: 2px;
          white-space: nowrap;
        }

        .table-user {
          gap: 10px;
          min-width: 0;
        }

        .table-avatar {
          flex: none;
        }

        .table-user-copy,
        .table-department,
        .table-weekly-quota {
          display: flex;
          flex-direction: column;
          gap: 2px;
          min-width: 0;
        }

        .table-user-name {
          overflow: hidden;
          color: var(--gray-900);
          font-size: 13px;
          font-weight: 600;
          text-overflow: ellipsis;
          white-space: nowrap;
        }

        .table-weekly-primary {
          overflow: hidden;
          color: var(--gray-800);
          font-size: 13px;
          font-weight: 500;
          text-overflow: ellipsis;
          white-space: nowrap;
        }

        .table-weekly-line {
          display: flex;
          align-items: baseline;
          gap: 6px;
        }

        .table-weekly-percent {
          color: var(--gray-500);
          font-size: 12px;
          font-weight: 500;
          font-variant-numeric: tabular-nums;
        }

        .table-weekly-progress {
          margin: 0;
          line-height: 1;
        }

        .table-weekly-unlimited {
          color: var(--gray-500);
        }

        .table-user-id,
        .table-time,
        .table-secondary {
          overflow: hidden;
          color: var(--gray-600);
          font-size: 12px;
          text-overflow: ellipsis;
          white-space: nowrap;
        }

        .table-user-id {
          font-family: 'Monaco', 'Consolas', monospace;
        }

        .user-status {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          min-width: 52px;
          padding: 2px 8px;
          border-radius: 999px;
          font-size: 12px;
          font-weight: 500;
          line-height: 20px;
        }

        .user-status-active {
          background: var(--color-success-50);
          color: var(--color-success-700);
        }

        .user-status-disabled {
          background: var(--gray-100);
          color: var(--gray-600);
        }

        .table-action-btn {
          height: 28px;
          padding: 0 5px;
          color: var(--gray-600);
          font-size: 12px;

          &:hover {
            color: var(--gray-900);
          }
        }

        .table-action-activate {
          color: var(--color-success-700);

          &:hover {
            color: var(--color-success-700);
            background: var(--color-success-50);
          }
        }
      }

      .pagination-section {
        display: flex;
        justify-content: flex-end;
        margin-top: 16px;
      }
    }
  }

  .time-text {
    font-size: 13px;
    color: var(--gray-700);
  }

  .phone-text,
  .user-id-text {
    font-size: 13px;
    color: var(--gray-900);
    font-family: 'Monaco', 'Consolas', monospace;
  }
}

@keyframes spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}

.user-modal {
  :deep(.ant-modal-header) {
    padding: 20px 24px 16px;
    border-bottom: 1px solid var(--gray-150);

    .ant-modal-title {
      font-size: 17px;
      font-weight: 600;
      color: var(--gray-900);
    }
  }

  :deep(.ant-modal-body) {
    padding: 20px 24px 24px;
  }

  .user-form {
    .form-item {
      margin-bottom: 16px;

      :deep(.ant-form-item-label) {
        padding-bottom: 6px;

        label {
          font-weight: 600;
          font-size: 13px;
          color: var(--gray-800);
        }
      }
    }

    .error-text {
      color: var(--color-error-500);
      font-size: 12px;
      margin-top: 4px;
      line-height: 1.3;
    }

    .help-text {
      color: var(--gray-600);
      font-size: 12px;
      margin-top: 4px;
      line-height: 1.3;
    }

    .quota-form-group {
      display: flex;
      flex-direction: column;
      gap: 8px;
    }

    .quota-input {
      width: 100%;
    }

    .password-toggle {
      margin-bottom: 16px;
      padding: 12px 16px;
      background: var(--gray-25);
      border-radius: 8px;
      border: 1px solid var(--gray-100);

      :deep(.ant-checkbox-wrapper) {
        font-weight: 500;
        color: var(--gray-700);
        font-size: 13px;
      }
    }
  }
}

.global-reset-modal {
  display: flex;
  flex-direction: column;
  gap: 12px;

  .global-reset-summary {
    margin: 0;
    color: var(--gray-800);
    line-height: 1.6;
  }

  .global-reset-hint {
    margin: 0;
    color: var(--gray-600);
    font-size: 13px;
    line-height: 1.6;
  }
}
</style>
