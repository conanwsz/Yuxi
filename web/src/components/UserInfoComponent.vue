<template>
  <div class="user-info-component">
    <a-dropdown
      :trigger="['click']"
      v-if="userStore.isLoggedIn"
      :placement="placement"
      @visibleChange="handleDropdownVisibleChange"
    >
      <div class="user-info-dropdown" :data-align="showRole ? 'left' : 'center'">
        <div class="user-avatar">
          <FallbackAvatar
            :src="userStore.avatar"
            :default-src="avatarDefaultSrc"
            :name="userStore.username"
            :seed="userStore.uid || userStore.username"
            kind="user"
            :size="32"
            shape="circle"
            :alt="userStore.username"
            class="avatar-image"
          />
          <!-- <div class="user-role-badge" :class="userRoleClass"></div> -->
        </div>
        <div v-if="showRole" class="user-name">{{ userStore.username }}</div>
        <div v-if="slots.actions" class="user-info-actions">
          <slot name="actions" />
        </div>
      </div>
      <template #overlay>
        <a-menu>
          <a-menu-item key="user-info" @click="openProfile">
            <div class="user-info-display">
              <div class="user-menu-username">{{ userStore.username }}</div>
              <div class="user-menu-details">
                <span class="user-menu-info">ID: {{ userStore.uid }}</span>
                <span class="user-menu-role">{{ userRoleText }}</span>
              </div>
            </div>
          </a-menu-item>
          <a-menu-divider />
          <div class="user-token-quota" :data-mode="tokenQuotaState">
            <!-- 加载中 -->
            <template v-if="tokenQuotaLoading">
              <div class="token-row">
                <div class="token-row-top">
                  <span class="token-title">
                    <Zap :size="14" />
                    Token 用量
                  </span>
                  <span class="token-loading">加载中…</span>
                </div>
                <div class="token-progress token-progress-skeleton"></div>
              </div>
            </template>
            <!-- 加载失败 -->
            <template v-else-if="tokenQuotaError">
              <div class="token-row">
                <div class="token-row-top">
                  <span class="token-title">
                    <Zap :size="14" />
                    Token 用量
                  </span>
                  <span class="token-error">⚠ 数据不可用</span>
                </div>
                <div class="token-meta-line">点击设置页可手动刷新</div>
              </div>
            </template>
            <!-- 不限模式 -->
            <template v-else-if="tokenQuota.mode === 'unlimited'">
              <div class="token-row">
                <div class="token-row-top">
                  <span class="token-title">
                    <Zap :size="14" />
                    Token 用量
                  </span>
                  <span class="token-unlimited-tag">不限</span>
                </div>
                <div class="token-numbers">
                  本周已用
                  <strong>{{ formatQuotaMetric(tokenQuota.used) }}</strong>
                  <span class="suffix">Token</span>
                </div>
                <div v-if="tokenQuota.resetAt" class="token-reset">
                  重置于 {{ tokenQuota.resetAt }}
                </div>
              </div>
            </template>
            <!-- 标准 / 自定义配额 -->
            <template v-else>
              <div class="token-row">
                <div class="token-row-top">
                  <span class="token-title">
                    <Zap :size="14" />
                    Token 用量
                  </span>
                  <span class="token-pct">{{ quotaUsedPercent }}%</span>
                </div>
                <div class="token-progress">
                  <div
                    class="token-progress-fill"
                    :style="{ width: quotaUsedPercent + '%', background: quotaProgressColor }"
                  ></div>
                </div>
                <div class="token-numbers">
                  <strong>{{ formatQuotaMetric(tokenQuota.used) }}</strong>
                  <span class="suffix">/ {{ formatQuotaMetric(tokenQuota.quota) }} Token</span>
                </div>
                <div v-if="tokenQuota.resetAt" class="token-reset">
                  重置于 {{ tokenQuota.resetAt }}
                </div>
              </div>
            </template>
          </div>
          <a-menu-divider />
          <a-menu-item key="theme" @click="toggleTheme">
            <template #icon>
              <Sun v-if="themeStore.isDark" :size="16" />
              <Moon v-else :size="16" />
            </template>
            <span class="menu-text">{{
              themeStore.isDark ? '切换到浅色模式' : '切换到深色模式'
            }}</span>
          </a-menu-item>
          <a-menu-divider />
          <a-menu-item v-if="userStore.isSuperAdmin" key="debug" @click="showDebug = true">
            <template #icon><Terminal :size="16" /></template>
            <span class="menu-text">调试面板（非生产环境）</span>
          </a-menu-item>
          <a-menu-item key="setting" @click="goToSetting">
            <template #icon><Settings :size="16" /></template>
            <span class="menu-text">设置</span>
          </a-menu-item>
          <a-menu-item key="logout" @click="logout">
            <template #icon><LogOut :size="16" /></template>
            <span class="menu-text">退出登录</span>
          </a-menu-item>
        </a-menu>
      </template>
    </a-dropdown>
    <a-button v-else-if="showButton" type="primary" @click="goToLogin"> 登录 </a-button>

    <!-- 调试面板 Modal -->
    <DebugComponent v-model:show="showDebug" />
  </div>
</template>

<script setup>
import { computed, ref, inject, useSlots, watch, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user'
import DebugComponent from '@/components/DebugComponent.vue'
import { message } from 'ant-design-vue'
import { Zap, Sun, Moon, LogOut, Settings, Terminal } from 'lucide-vue-next'
import { useThemeStore } from '@/stores/theme'
import { generatePixelAvatar } from '@/utils/pixelAvatar'
import FallbackAvatar from '@/components/common/FallbackAvatar.vue'
import { userApi } from '@/apis/user_api'

const router = useRouter()
const userStore = useUserStore()
const themeStore = useThemeStore()
const slots = useSlots()

// 调试面板状态
const showDebug = ref(false)

// Inject settings modal methods
const { openSettingsModal } = inject('settingsModal', {})

const avatarDefaultSrc = computed(() => (userStore.uid ? generatePixelAvatar(userStore.uid) : ''))

defineProps({
  showRole: {
    type: Boolean,
    default: false
  },
  showButton: {
    type: Boolean,
    default: false
  },
  // a-dropdown 的 placement；侧边栏底部 trigger 应传 'topLeft'，
  // 顶部 trigger 保持默认 'bottomLeft'。指定后 vc-dropdown 不会再做
  // overflow adjust flip，避免「先在底部弹出再跳到顶部」的视觉闪烁。
  placement: {
    type: String,
    default: 'bottomLeft'
  }
})

// 用户角色显示文本
const userRoleText = computed(() => {
  switch (userStore.userRole) {
    case 'superadmin':
      return '超级管理员'
    case 'admin':
      return '管理员'
    case 'user':
      return '普通用户'
    default:
      return userStore.roleName || userStore.userRole || '未知角色'
  }
})

// 退出登录
const logout = () => {
  userStore.logout()
  message.success('已退出登录')
  // 跳转到首页
  router.push('/login')
}

// 前往登录页
const goToLogin = () => {
  router.push('/login')
}

const toggleTheme = () => {
  themeStore.toggleTheme()
}

// 前往设置页
const goToSetting = () => {
  if (openSettingsModal) {
    openSettingsModal('account')
  }
}

const openProfile = () => {
  if (openSettingsModal) {
    openSettingsModal('account')
  }
}

// ─── 个人 Token 用量（菜单内展示） ─────────────────────────────────
// 数据结构与 SettingsModal.vue:325-411 一致；此处仅取菜单内展示所需的
// 几个工具函数。如未来两边都需要维护，可抽到 composables/useTokenQuota.js。
const tokenQuota = ref({
  mode: 'inherit',
  quota: null,
  used: null,
  remaining: null,
  by_model: [],
  models: [],
  resetAt: ''
})
const tokenQuotaLoading = ref(false)
const tokenQuotaError = ref('')
const hasLoadedTokenQuota = ref(false)

const tokenQuotaState = computed(() => {
  if (tokenQuotaLoading.value) return 'loading'
  if (tokenQuotaError.value) return 'error'
  if (tokenQuota.value.mode === 'unlimited') return 'unlimited'
  return 'normal'
})

const quotaUsedPercent = computed(() => {
  if (tokenQuota.value.mode === 'unlimited') return 0
  const quota = tokenQuota.value.quota
  const used = tokenQuota.value.used
  if (!Number.isFinite(quota) || quota <= 0 || !Number.isFinite(used)) return 0
  return Math.max(0, Math.min(100, Math.round((used / quota) * 100)))
})

const quotaProgressColor = computed(() => {
  const percent = quotaUsedPercent.value
  if (percent >= 90) return 'var(--color-error-500)'
  if (percent >= 70) return 'var(--color-warning-500)'
  return 'var(--main-500)'
})

const getQuotaPayload = (response) => {
  const payload = response?.data && typeof response.data === 'object' ? response.data : response
  if (!payload || typeof payload !== 'object') return {}
  return payload.token_quota && typeof payload.token_quota === 'object'
    ? payload.token_quota
    : payload
}

const normalizeQuotaNumber = (value) => {
  if (value === null || typeof value === 'undefined' || value === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed >= 0 ? Math.round(parsed) : null
}

const formatQuotaMetric = (value) => {
  if (tokenQuota.value.mode === 'unlimited' && value === null) return '不限'
  if (!Number.isFinite(value)) return '-'
  return new Intl.NumberFormat('zh-CN').format(value)
}

const loadTokenQuota = async ({ silent = false } = {}) => {
  if (!silent) tokenQuotaLoading.value = true
  tokenQuotaError.value = ''
  try {
    const response = await userApi.getTokenQuota()
    const payload = getQuotaPayload(response)
    tokenQuota.value = {
      mode: ['inherit', 'custom', 'unlimited'].includes(payload.mode) ? payload.mode : 'inherit',
      quota: normalizeQuotaNumber(payload.effective_quota),
      used: normalizeQuotaNumber(payload.used),
      remaining: normalizeQuotaNumber(payload.remaining),
      by_model: Array.isArray(payload.by_model) ? payload.by_model : [],
      models: Array.isArray(payload.models) ? payload.models : [],
      resetAt: payload.reset_at || ''
    }
  } catch (error) {
    tokenQuotaError.value = error.message || 'Token 用量暂时不可用'
  } finally {
    tokenQuotaLoading.value = false
  }
}

// 组件挂载即拉一次，避免首次点开菜单时「骨架 → 完整卡片」造成的菜单高度跳动。
// handleDropdownVisibleChange 仍保留作为 fallback（应对组件挂载后立即被点开等极端时序）。
onMounted(() => {
  if (!hasLoadedTokenQuota.value) {
    hasLoadedTokenQuota.value = true
    loadTokenQuota()
  }
})

// 首次展开下拉时拉一次；下拉收起时静默刷新，下一次展开拿到的就是新数据。
const handleDropdownVisibleChange = (visible) => {
  if (visible && !hasLoadedTokenQuota.value) {
    hasLoadedTokenQuota.value = true
    loadTokenQuota()
    return
  }
  if (!visible && hasLoadedTokenQuota.value) {
    loadTokenQuota({ silent: true })
  }
}

// 监听登录态：登出时清掉缓存，避免下次登录仍显示上一账号的数据
watch(
  () => userStore.isLoggedIn,
  (loggedIn) => {
    if (!loggedIn) {
      hasLoadedTokenQuota.value = false
      tokenQuota.value = {
        mode: 'inherit',
        quota: null,
        used: null,
        remaining: null,
        by_model: [],
        models: [],
        resetAt: ''
      }
      tokenQuotaError.value = ''
    }
  }
)
</script>

<style lang="less" scoped>
.user-info-component {
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--gray-800);
  font-family:
    -apple-system, BlinkMacSystemFont, 'Noto Sans SC', 'Roboto', 'HarmonyOS Sans SC', 'Segoe UI',
    'Helvetica Neue', Arial, sans-serif;
}

.user-info-dropdown {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;

  &[data-align='center'] {
    justify-content: center;
  }

  &[data-align='left'] {
    justify-content: flex-start;
  }
}

.user-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.user-info-actions {
  display: inline-flex;
  align-items: center;
  margin-left: auto;
}

.user-avatar {
  @avatar-size: 32px;
  width: @avatar-size;
  height: @avatar-size;
  min-width: @avatar-size;
  flex: 0 0 @avatar-size;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: bold;
  font-size: 16px;
  cursor: pointer;
  position: relative;
  overflow: hidden;
  box-shadow: 0 2px 8px var(--shadow-1);

  &:hover {
    opacity: 0.9;
  }

  .avatar-image {
    width: 100%;
    height: 100%;
    display: block;
    box-sizing: border-box;
    object-fit: cover;
    border-radius: 50%;
    border: 2px solid var(--gray-150);
  }
}

.user-role-badge {
  position: absolute;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  right: 0;
  bottom: 0;
  border: 2px solid var(--gray-0);

  &.superadmin {
    background-color: var(--color-warning-500);
  }

  &.admin {
    background-color: var(--color-info-500); /* 蓝色，管理员 */
  }

  &.user {
    background-color: var(--color-success-500); /* 绿色，普通用户 */
  }
}

.user-info-display {
  line-height: 1.4;
}

.user-menu-username {
  font-weight: 600;
  color: var(--gray-900);
  font-size: 14px;
  display: block;
  margin-bottom: 2px;
}

.user-menu-details {
  display: flex;
  gap: 12px;
  align-items: center;
}

.user-menu-info {
  font-size: 12px;
  color: var(--gray-600);
}

.user-menu-role {
  font-size: 12px;
  color: var(--gray-500);
}

.login-icon {
  width: 30px;
  height: 30px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  border-radius: 50%;
  transition:
    background-color 0.2s,
    color 0.2s;
  color: var(--gray-900);

  &:hover {
    background-color: var(--main-10);
    color: var(--main-color);
  }
}

:deep(.ant-dropdown-menu) {
  padding: 8px 0;
}

:deep(.ant-dropdown-menu-title-content) {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--gray-900);
}

:deep(.ant-dropdown-menu-item svg) {
  margin-right: 4px;
  color: var(--gray-900);
  vertical-align: middle;
}

.menu-text {
  line-height: 20px;
}

/* ─── 个人 Token 用量 ─────────────────────────────────────── */
.user-token-quota {
  padding: 10px 14px 12px;
  cursor: default;
  user-select: none;
}

.token-row {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.token-row-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}

.token-title {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: var(--gray-900);
  font-size: 13px;
  font-weight: 500;
}

.token-title svg {
  color: var(--main-color);
  flex: 0 0 auto;
}

.token-pct {
  font-size: 12px;
  color: var(--gray-600);
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}

.token-loading {
  font-size: 12px;
  color: var(--gray-500);
}

.token-error {
  font-size: 12px;
  color: var(--color-warning-500);
}

.token-meta-line {
  font-size: 11px;
  color: var(--gray-500);
}

.token-unlimited-tag {
  display: inline-flex;
  align-items: center;
  background: #f6ffed;
  color: var(--color-success-500);
  border: 1px solid #b7eb8f;
  padding: 1px 8px;
  border-radius: 10px;
  font-size: 11px;
  font-weight: 600;
  line-height: 16px;
}

.token-progress {
  height: 4px;
  border-radius: 2px;
  background: var(--gray-150);
  overflow: hidden;
}

.token-progress-fill {
  height: 100%;
  border-radius: 2px;
  transition: width 0.3s ease;
}

.token-progress-skeleton {
  background: linear-gradient(
    90deg,
    var(--gray-150) 0%,
    var(--gray-100, #e8e8e8) 50%,
    var(--gray-150) 100%
  );
  background-size: 200% 100%;
  animation: token-progress-shimmer 1.2s linear infinite;
}

@keyframes token-progress-shimmer {
  0% {
    background-position: 200% 0;
  }
  100% {
    background-position: -200% 0;
  }
}

.token-numbers {
  display: inline-flex;
  align-items: baseline;
  gap: 4px;
  color: var(--gray-700);
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}

.token-numbers strong {
  color: var(--gray-900);
  font-size: 16px;
  font-weight: 600;
  line-height: 1.2;
}

.token-numbers .suffix {
  color: var(--gray-500);
  font-size: 12px;
  font-weight: 400;
}

.token-reset {
  font-size: 12px;
  color: var(--gray-500);
}
</style>
