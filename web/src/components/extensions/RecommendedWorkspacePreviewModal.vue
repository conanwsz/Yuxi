<template>
  <a-modal
    :open="props.open"
    @update:open="(val) => emit('update:open', val)"
    :footer="null"
    width="680px"
    :closable="!installing"
    :destroy-on-close="true"
    :mask-closable="!installing"
    class="recommended-workspace-preview-modal"
    :title="modalTitle"
    @cancel="handleClose"
  >
    <div v-if="props.skill" class="preview-panel">
      <div class="preview-header">
        <div class="preview-icon">
          <component :is="getSkillIcon(props.skill.slug)" :size="18" />
        </div>
        <div class="preview-title-text">
          <div class="preview-title">{{ formatExtensionCardTitle(props.skill.name) }}</div>
          <div class="preview-meta">
            <span
              >{{ sourceTypeLabel(props.skill.source_type || props.skill.sourceType) }} Skill</span
            >
            <span v-if="uploaderLabel" class="uploader-tag">由 {{ uploaderLabel }} 上传</span>
            <span v-if="props.skill.enabled === false" class="disabled-tag">已禁用</span>
          </div>
        </div>
      </div>

      <a-tabs v-model:activeKey="activeTab" class="preview-tabs">
        <a-tab-pane key="info" tab="概览">
          <p class="preview-description">{{ props.skill.description || '暂无描述' }}</p>
          <div class="info-grid">
            <div class="info-cell">
              <span class="info-label">slug</span>
              <span class="info-value">{{ props.skill.slug }}</span>
            </div>
            <div class="info-cell">
              <span class="info-label">工具依赖</span>
              <span class="info-value">
                {{ (props.skill.tool_dependencies || []).join('、') || '无' }}
              </span>
            </div>
            <div class="info-cell">
              <span class="info-label">MCP 依赖</span>
              <span class="info-value">
                {{ (props.skill.mcp_dependencies || []).join('、') || '无' }}
              </span>
            </div>
            <div class="info-cell">
              <span class="info-label">Skill 依赖</span>
              <span class="info-value">
                {{ (props.skill.skill_dependencies || []).join('、') || '无' }}
              </span>
            </div>
          </div>
        </a-tab-pane>
        <a-tab-pane key="skill_md" tab="SKILL.md">
          <div v-if="markdownLoading" class="preview-loading"><a-spin /></div>
          <MarkdownPreview v-else-if="markdownContent" :content="markdownContent" :compact="true" />
          <a-empty v-else :description="markdownError || '未读取到 SKILL.md'" />
        </a-tab-pane>
      </a-tabs>

      <div class="preview-footer">
        <a-button @click="handleClose" :disabled="installing">关闭</a-button>
        <a-space>
          <a-button
            type="primary"
            :loading="installing && installingTarget === 'personal'"
            :disabled="installing || props.skill.enabled === false"
            @click="handleInstallToPersonal"
          >
            安装到个人工作区
          </a-button>
          <a-button
            v-if="userStore.isAdmin"
            :loading="installing && installingTarget === 'shared'"
            :disabled="installing || props.skill.enabled === false"
            @click="handleInstallToShared"
          >
            安装到共享 Skill
          </a-button>
        </a-space>
      </div>
    </div>
  </a-modal>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import { message } from 'ant-design-vue'
import { skillApi } from '@/apis/skill_api'
import { useUserStore } from '@/stores/user'
import MarkdownPreview from '@/components/common/MarkdownPreview.vue'
import { getSkillIcon } from '@/utils/skill_icon_utils'
import { formatExtensionCardTitle } from '@/utils/extensionDisplayName'

const props = defineProps({
  open: { type: Boolean, default: false },
  skill: { type: Object, default: null }
})

const emit = defineEmits(['update:open', 'close', 'installed'])

const userStore = useUserStore()
const activeTab = ref('info')
const markdownContent = ref('')
const markdownLoading = ref(false)
const markdownError = ref('')
const installing = ref(false)
const installingTarget = ref(null)

const modalTitle = computed(() =>
  props.skill ? `「推荐」Skill · ${props.skill.name}` : '推荐 Skill'
)

const uploaderLabel = computed(() => {
  const u = props.skill?.created_by || props.skill?.createdBy
  return u ? String(u) : ''
})

const sourceTypeLabel = (st) => {
  if (st === 'personal') return '个人技能'
  if (st === 'builtin') return '内置'
  if (st === 'remote') return '远程'
  return '上传'
}

const loadSkillMarkdown = async () => {
  if (!props.skill?.slug) return
  markdownLoading.value = true
  markdownError.value = ''
  try {
    const res = await skillApi.getSkillFile(props.skill.slug, 'SKILL.md')
    markdownContent.value = res?.data?.content || ''
  } catch (err) {
    markdownError.value = err?.response?.data?.detail || err?.message || '读取 SKILL.md 失败'
  } finally {
    markdownLoading.value = false
  }
}

const handleClose = () => {
  if (installing.value) return
  emit('update:open', false)
  emit('close')
}

const handleInstallToPersonal = async () => {
  if (!props.skill?.slug) return
  installing.value = true
  installingTarget.value = 'personal'
  try {
    await skillApi.installRecommendedWorkspaceToPersonal(props.skill.slug)
    message.success('已安装到个人工作区')
    emit('installed', { slug: props.skill.slug, target: 'personal' })
    emit('update:open', false)
  } catch (err) {
    const detail = err?.response?.data?.detail || err?.message || '安装失败'
    message.error(detail)
  } finally {
    installing.value = false
    installingTarget.value = null
  }
}

const handleInstallToShared = () => {
  // v1 暂不实现：仅装机人/admin 已经在「共享」分组中可管理；其他用户装到共享需新一轮 share_config 流程。
  message.info('「从推荐工作区装到共享」功能计划在 v1.1 提供，请先安装到个人工作区再升级为共享。')
}

watch(
  () => [props.open, props.skill?.slug],
  ([open]) => {
    if (open) {
      activeTab.value = 'info'
      markdownContent.value = ''
      markdownError.value = ''
      if (activeTab.value === 'info') {
        // 暂不主动加载 markdown；等用户切到「SKILL.md」标签再加载
      }
    }
  },
  { immediate: true }
)

watch(activeTab, (tab) => {
  if (tab === 'skill_md' && !markdownContent.value && !markdownLoading.value) {
    loadSkillMarkdown()
  }
})
</script>

<style lang="less" scoped>
@import '@/assets/css/extensions.less';

.recommended-workspace-preview-modal {
  .preview-panel {
    .preview-header {
      display: flex;
      align-items: flex-start;
      gap: 11px;
      padding-right: 28px;
      margin-bottom: 12px;
    }

    .preview-icon {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      flex: 0 0 34px;
      height: 34px;
      border-radius: 8px;
      background: var(--gray-100);
      color: var(--gray-700);
    }

    .preview-title-text {
      min-width: 0;
    }

    .preview-title {
      font-size: 17px;
      font-weight: 650;
      line-height: 24px;
      color: var(--gray-900);
    }

    .preview-meta {
      display: flex;
      gap: 8px;
      margin-top: 4px;
      color: var(--gray-500);
      font-size: 12px;

      .uploader-tag {
        padding: 1px 8px;
        border-radius: 999px;
        background: var(--gray-100);
        color: var(--gray-700);
      }
      .disabled-tag {
        padding: 1px 8px;
        border-radius: 999px;
        background: #fff1f0;
        color: #cf1322;
      }
    }

    .preview-tabs {
      min-height: 240px;
    }

    .preview-description {
      margin: 8px 0 16px;
      color: var(--gray-700);
      font-size: 14px;
      line-height: 22px;
    }

    .info-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px 16px;
    }

    .info-cell {
      display: flex;
      flex-direction: column;
      gap: 2px;
      padding: 10px 12px;
      border-radius: 6px;
      background: var(--gray-25);
    }

    .info-label {
      color: var(--gray-500);
      font-size: 12px;
    }

    .info-value {
      color: var(--gray-800);
      font-size: 13px;
      word-break: break-all;
    }

    .preview-loading {
      display: flex;
      justify-content: center;
      padding: 24px 0;
    }

    .preview-footer {
      display: flex;
      justify-content: flex-end;
      gap: 8px;
      margin-top: 16px;
      padding-top: 12px;
      border-top: 1px solid var(--gray-150);
    }
  }
}
</style>
