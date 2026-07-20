<template>
  <a-modal
    v-model:open="visible"
    :title="null"
    width="calc(100vw - 48px)"
    :style="{ maxWidth: '1600px', minWidth: '320px', top: '24px', paddingBottom: '24px' }"
    :footer="null"
    :closable="false"
    @cancel="handleClose"
    class="settings-modal"
    :destroyOnClose="true"
    :bodyStyle="{ padding: 0 }"
  >
    <div class="settings-container">
      <button class="settings-close-btn lucide-icon-btn" @click="handleClose" aria-label="关闭设置">
        <X :size="16" />
      </button>

      <!-- 侧边栏 (Desktop) -->
      <div class="settings-sider">
        <div class="settings-sider-nav">
          <div
            class="sider-item"
            :class="{ activesec: activeTab === 'account' }"
            @click="activeTab = 'account'"
            v-if="userStore.isLoggedIn"
          >
            <CircleUser class="icon" :size="18" />
            <span>账户设置</span>
          </div>
          <div
            class="sider-item"
            :class="{ activesec: activeTab === 'apiKeys' }"
            @click="activeTab = 'apiKeys'"
            v-if="userStore.isLoggedIn"
          >
            <Key class="icon" :size="18" />
            <span>API Keys</span>
          </div>
          <div
            class="sider-item"
            :class="{ activesec: activeTab === 'base' }"
            @click="activeTab = 'base'"
            v-if="userStore.hasPermission('system.config.read')"
          >
            <Settings class="icon" :size="18" />
            <span>基本设置</span>
          </div>
          <div
            class="sider-item"
            :class="{ activesec: activeTab === 'user' }"
            @click="activeTab = 'user'"
            v-if="userStore.hasPermission('users.read')"
          >
            <User class="icon" :size="18" />
            <span>用户管理</span>
          </div>
          <div
            class="sider-item"
            :class="{ activesec: activeTab === 'department' }"
            @click="activeTab = 'department'"
            v-if="userStore.hasPermission('departments.read')"
          >
            <Users class="icon" :size="18" />
            <span>部门管理</span>
          </div>
          <div
            class="sider-item"
            :class="{ activesec: activeTab === 'permission' }"
            @click="activeTab = 'permission'"
            v-if="userStore.isSuperAdmin"
          >
            <ShieldCheck class="icon" :size="18" />
            <span>权限管理</span>
          </div>
          <div
            class="sider-item"
            :class="{ activesec: activeTab === 'agentEnv' }"
            @click="activeTab = 'agentEnv'"
            v-if="userStore.isLoggedIn"
          >
            <SquareTerminal class="icon" :size="18" />
            <span>环境变量</span>
          </div>
        </div>

        <div v-if="showStarCard" class="settings-star-card">
          <div class="star-card-header">
            <div class="star-card-badge">
              <Star :size="12" />
              <span>支持项目</span>
            </div>
            <button
              class="star-card-close lucide-icon-btn"
              @click="dismissStarCard"
              aria-label="关闭 Star 提示"
            >
              <X :size="14" />
            </button>
          </div>
          <p class="star-card-title">给 Yuxi 点个 Star</p>
          <p class="star-card-description">
            如果这个项目帮到了你，欢迎去 GitHub 点亮一个 Star，让更多人看到它。
          </p>
          <a
            class="star-card-link"
            :href="projectRepoUrl"
            target="_blank"
            rel="noopener noreferrer"
          >
            <img
              class="star-card-link-image"
              src="https://img.shields.io/github/stars/xerrors/Yuxi?label=Yuxi&style=social"
              alt="GitHub stars for Yuxi"
            />
            <ExternalLink :size="13" />
          </a>
        </div>
      </div>

      <!-- 顶部导航 (Mobile) -->
      <div class="settings-mobile-nav">
        <div
          class="nav-item"
          :class="{ active: activeTab === 'account' }"
          @click="activeTab = 'account'"
          v-if="userStore.isLoggedIn"
        >
          账户设置
        </div>
        <div
          class="nav-item"
          :class="{ active: activeTab === 'apiKeys' }"
          @click="activeTab = 'apiKeys'"
          v-if="userStore.isLoggedIn"
        >
          API Keys
        </div>
        <div
          class="nav-item"
          :class="{ active: activeTab === 'agentEnv' }"
          @click="activeTab = 'agentEnv'"
          v-if="userStore.isLoggedIn"
        >
          沙盒环境变量
        </div>
        <div
          class="nav-item"
          :class="{ active: activeTab === 'base' }"
          @click="activeTab = 'base'"
          v-if="userStore.hasPermission('system.config.read')"
        >
          基本设置
        </div>
        <div
          class="nav-item"
          :class="{ active: activeTab === 'user' }"
          @click="activeTab = 'user'"
          v-if="userStore.hasPermission('users.read')"
        >
          用户管理
        </div>
        <div
          class="nav-item"
          :class="{ active: activeTab === 'department' }"
          @click="activeTab = 'department'"
          v-if="userStore.hasPermission('departments.read')"
        >
          部门管理
        </div>
        <div
          class="nav-item"
          :class="{ active: activeTab === 'permission' }"
          @click="activeTab = 'permission'"
          v-if="userStore.isSuperAdmin"
        >
          权限管理
        </div>
      </div>

      <!-- 内容区域 -->
      <div class="settings-content-wrapper">
        <div class="settings-content">
          <div v-show="activeTab === 'account'" v-if="userStore.isLoggedIn">
            <AccountSettingsComponent />
          </div>

          <div v-if="activeTab === 'apiKeys' && userStore.isLoggedIn">
            <ApiKeyManagementComponent />
          </div>

          <div v-if="activeTab === 'agentEnv' && userStore.isLoggedIn">
            <AgentEnvSettingsCard />
          </div>

          <div v-show="activeTab === 'base'" v-if="userStore.hasPermission('system.config.read')">
            <BasicSettingsSection />
          </div>

          <div v-show="activeTab === 'user'" v-if="userStore.hasPermission('users.read')">
            <UserManagementComponent />
          </div>

          <div v-show="activeTab === 'department'" v-if="userStore.hasPermission('departments.read')">
            <DepartmentManagementComponent />
          </div>

          <div v-show="activeTab === 'permission'" v-if="userStore.isSuperAdmin">
            <PermissionManagementComponent />
          </div>
        </div>
      </div>
    </div>
  </a-modal>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useUserStore } from '@/stores/user'
import {
  CircleUser,
  ExternalLink,
  Settings,
  Key,
  Star,
  SquareTerminal,
  ShieldCheck,
  User,
  Users,
  X
} from 'lucide-vue-next'
import AccountSettingsComponent from '@/components/AccountSettingsComponent.vue'
import AgentEnvSettingsCard from '@/components/AgentEnvSettingsCard.vue'
import BasicSettingsSection from '@/components/BasicSettingsSection.vue'
import ApiKeyManagementComponent from '@/components/ApiKeyManagementComponent.vue'
import UserManagementComponent from '@/components/UserManagementComponent.vue'
import DepartmentManagementComponent from '@/components/DepartmentManagementComponent.vue'
import PermissionManagementComponent from '@/components/PermissionManagementComponent.vue'

const props = defineProps({
  visible: {
    type: Boolean,
    default: false
  },
  initialTab: {
    type: String,
    default: ''
  }
})

const emit = defineEmits(['update:visible', 'close'])

const userStore = useUserStore()
const activeTab = ref('account')
const showStarCard = ref(true)

const STAR_CARD_STORAGE_KEY = 'yuxi-settings-star-card-dismissed'
const projectRepoUrl = 'https://github.com/xerrors/Yuxi'

const visible = computed({
  get: () => props.visible,
  set: (value) => emit('update:visible', value)
})

const availableTabs = computed(() => {
  const tabs = []
  if (userStore.isLoggedIn) tabs.push('account', 'userConfig', 'agentEnv')
  if (userStore.hasPermission('system.config.read')) tabs.push('base')
  if (userStore.hasPermission('users.read')) tabs.push('user')
  if (userStore.hasPermission('departments.read')) tabs.push('department')
  if (userStore.isSuperAdmin) tabs.push('permission')
  return tabs
})

const setActiveTab = (preferredTab) => {
  if (preferredTab && availableTabs.value.includes(preferredTab)) {
    activeTab.value = preferredTab
    return
  }
  activeTab.value = availableTabs.value.includes('base') ? 'base' : availableTabs.value[0]
}

const handleClose = () => {
  emit('close')
}

const dismissStarCard = () => {
  showStarCard.value = false
  localStorage.setItem(STAR_CARD_STORAGE_KEY, 'true')
}

onMounted(() => {
  showStarCard.value = localStorage.getItem(STAR_CARD_STORAGE_KEY) !== 'true'
})

watch(
  () => [props.visible, props.initialTab],
  ([newVal]) => {
    if (newVal) {
      setActiveTab(props.initialTab)
    }
  }
)
</script>

<style lang="less">
.settings-modal.ant-modal {
  .ant-modal-content {
    border-radius: 12px;
    display: flex;
    flex-direction: column;
    position: relative;
    padding: 0;
    overflow: hidden;
  }

  .ant-modal-body {
    padding: 0;
  }
}

.settings-container {
  display: flex;
  height: calc(100vh - 48px);
  max-height: 920px;
  min-height: min(640px, calc(100vh - 48px));
  width: 100%;
  position: relative;

  @media (max-width: 900px) {
    flex-direction: column;
    height: calc(100vh - 16px);
    min-height: 0;
  }
}

@media (max-width: 900px) {
  .settings-modal.ant-modal {
    width: calc(100vw - 16px) !important;
    top: 8px !important;
    margin: 0 8px;
    padding-bottom: 8px !important;
  }
}

.settings-close-btn {
  position: absolute;
  top: 10px;
  left: 14px;
  width: 32px;
  height: 32px;
  border: none;
  border-radius: 8px;
  background: var(--gray-50);
  color: var(--gray-700);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  z-index: 2;

  &:hover {
    background: var(--gray-200);
    color: var(--gray-900);
  }
}

/* Sidebar Styles - Matching SettingView.vue style */
.settings-sider {
  width: 176px;
  height: 100%;
  padding: 52px 10px 12px;
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  background: var(--gray-50);
  border-right: 1px solid var(--gray-150);

  @media (max-width: 900px) {
    display: none;
  }

  .settings-sider-nav {
    display: flex;
    flex-direction: column;
    gap: 8px;
    width: 100%;
  }

  .sider-item {
    width: 100%;
    padding: 6px 12px; /* Matches SettingView .sider > * */
    cursor: pointer;
    transition: all 0.1s; /* Matches SettingView */
    text-align: left;
    font-size: 15px; /* Matches SettingView */
    border-radius: 8px;
    color: var(--gray-700);
    display: flex;
    align-items: center;
    gap: 10px;

    .icon {
      font-size: 14px; /* Slightly adjusted to align better, SettingView uses h() icon defaults */
    }

    &:hover {
      background: var(--gray-50);
    }

    &.activesec {
      background: var(--gray-150);
      color: var(--main-700);
    }
  }

  .settings-star-card {
    width: 100%;
    margin-top: auto;
    padding: 14px 12px 12px;
    border-radius: 12px;
    border: 1px solid rgba(4, 106, 130, 0.12);
    background:
      radial-gradient(circle at top right, rgba(95, 174, 194, 0.18), transparent 48%),
      linear-gradient(180deg, var(--main-5) 0%, var(--gray-0) 100%);
    overflow: hidden;
  }

  .star-card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
  }

  .star-card-badge {
    width: fit-content;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 8px;
    border-radius: 999px;
    background: rgba(4, 106, 130, 0.08);
    color: var(--main-700);
    font-size: 12px;
    font-weight: 600;
    line-height: 1;
  }

  .star-card-close {
    width: 24px;
    height: 24px;
    border: none;
    border-radius: 999px;
    background: rgba(255, 255, 255, 0.72);
    color: var(--gray-600);
    display: flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    flex-shrink: 0;

    &:hover {
      background: var(--gray-0);
      color: var(--gray-900);
    }
  }

  .star-card-title {
    margin: 10px 0 6px;
    color: var(--gray-900);
    font-size: 15px;
    font-weight: 600;
    line-height: 1.35;
  }

  .star-card-description {
    margin: 0;
    color: var(--gray-600);
    font-size: 12px;
    line-height: 1.5;
  }

  .star-card-link {
    margin-top: 12px;
    display: inline-flex;
    align-items: center;
    justify-content: flex-start;
    gap: 6px;
    text-decoration: none;
    color: var(--gray-600);
  }

  .star-card-link-image {
    display: block;
    height: 20px;
  }
}

/* Content Area */
.settings-content-wrapper {
  flex: 1;
  height: 100%;
  max-width: calc(100% - 176px);
  min-width: 0;
  display: flex;
  flex-direction: column;
  background: var(--gray-0);
  padding: 0;

  @media (max-width: 900px) {
    max-width: 100%;
    padding: 8px;
  }

  .settings-content {
    padding: 16px 16px; /* Keep inner readability without outer panel padding */
    // margin-bottom: 40px; /* Matches SettingView .setting margin-bottom */
    overflow-y: scroll;
    height: auto;
    flex: 1;
    min-height: 0;

    .model-providers-section,
    .user-management,
    .department-management,
    .apikey-management {
      min-height: auto;
    }

    .header-section {
      display: flex;
      justify-content: space-between;
      align-items: flex-end;
      gap: 16px;
      margin-bottom: 16px;
    }

    .header-content {
      flex: 1;
      min-width: 0;
    }

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

    .section-subtitle {
      margin: 0;
      font-size: 16px;
      font-weight: 500;
      color: var(--gray-900);
    }

    .add-btn {
      flex-shrink: 0;
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }

    @media (max-width: 900px) {
      height: auto;
      padding: 10px 12px 12px;
    }
  }
}

/* Mobile Styles */
.settings-mobile-nav {
  display: none;
  overflow-x: auto;
  border-bottom: 1px solid var(--gray-150);
  background: var(--gray-0);
  padding: 0;
  padding-left: 42px;
  flex-shrink: 0;

  @media (max-width: 900px) {
    display: flex;
  }

  .nav-item {
    padding: 12px 16px;
    white-space: nowrap;
    cursor: pointer;
    color: var(--gray-600);
    font-weight: 500;
    border-bottom: 2px solid transparent;
    transition: all 0.2s;

    &.active {
      color: var(--main-color);
      border-bottom-color: var(--main-color);
    }
  }
}
</style>
