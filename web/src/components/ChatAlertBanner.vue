<template>
  <div class="chat-alert-banner" :class="`kind-${kind}`" role="status">
    <div class="alert-icon">
      <component :is="iconComponent" :size="16" />
    </div>
    <div class="alert-content">
      <div class="alert-title">{{ title }}</div>
      <div class="alert-body">
        <slot>
          <span class="alert-message">{{ displayMessage }}</span>
          <span v-if="body?.quota != null" class="alert-meta">
            已用 {{ formatNumber(body.used) }} / {{ formatNumber(body.quota) }} · 下次重置
            {{ body.resetAt || '下个自然周' }}
          </span>
          <span v-if="body?.hint" class="alert-hint">{{ body.hint }}</span>
        </slot>
      </div>
    </div>
    <div class="alert-actions">
      <button
        v-if="canRetry"
        type="button"
        class="alert-action alert-retry"
        :aria-label="`重试：${title}`"
        @click="emit('retry', retry)"
      >
        <RefreshCw :size="14" />
        <span>重试</span>
      </button>
      <button
        v-if="dismissible"
        type="button"
        class="alert-action alert-dismiss"
        :aria-label="`关闭告警：${title}`"
        @click="emit('dismiss')"
      >
        <X :size="14" />
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { AlertTriangle, ShieldAlert, Info, X, RefreshCw } from 'lucide-vue-next'

const props = defineProps({
  kind: {
    type: String,
    default: 'generic_error'
  },
  title: {
    type: String,
    default: ''
  },
  body: {
    type: Object,
    default: () => ({})
  },
  /**
   * 重试载荷：{ text, imageContent, attachments, requestId }。
   * 通常由 useThreadAlerts 在 push 告警时挂到 body.retry。
   * 仅 quota_exceeded / generic_error 展示重试按钮；permission_denied 重试也是 403，无意义。
   */
  retry: {
    type: Object,
    default: null
  },
  dismissible: {
    type: Boolean,
    default: true
  }
})

const emit = defineEmits(['dismiss', 'retry'])

const canRetry = computed(
  () =>
    props.retry != null &&
    typeof props.retry === 'object' &&
    (props.kind === 'quota_exceeded' || props.kind === 'generic_error')
)

const iconComponent = computed(() => {
  switch (props.kind) {
    case 'quota_exceeded':
      return AlertTriangle
    case 'permission_denied':
      return ShieldAlert
    default:
      return Info
  }
})

const displayMessage = computed(() => props.body?.message || '')

const formatNumber = (n) => {
  if (n == null) return '-'
  if (typeof n !== 'number' || !Number.isFinite(n)) return String(n)
  return n.toLocaleString('en-US')
}
</script>

<style lang="less" scoped>
.chat-alert-banner {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin: 8px 16px 12px;
  padding: 10px 12px;
  border-radius: 8px;
  border: 1px solid transparent;
  font-size: 13px;
  line-height: 1.5;

  .alert-icon {
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    margin-top: 1px;
  }

  .alert-content {
    flex: 1;
    min-width: 0;
  }

  .alert-title {
    font-weight: 600;
    margin-bottom: 2px;
  }

  .alert-body {
    color: inherit;
    opacity: 0.92;
    word-break: break-word;
  }

  .alert-message {
    font-weight: 500;
  }

  .alert-meta,
  .alert-hint {
    display: block;
    margin-top: 4px;
    font-size: 12px;
    opacity: 0.85;
  }

  .alert-actions {
    flex-shrink: 0;
    display: flex;
    align-items: center;
    gap: 4px;
  }

  .alert-action {
    background: transparent;
    border: 1px solid transparent;
    padding: 4px 8px;
    cursor: pointer;
    color: inherit;
    opacity: 0.75;
    display: inline-flex;
    align-items: center;
    gap: 4px;
    border-radius: 4px;
    font-size: 12px;
    line-height: 1;
    transition:
      opacity 0.15s ease,
      background 0.15s ease,
      border-color 0.15s ease;

    &:hover {
      opacity: 1;
      background: rgba(0, 0, 0, 0.06);
    }

    &:focus-visible {
      outline: none;
      border-color: currentColor;
      opacity: 1;
    }
  }

  .alert-retry {
    font-weight: 500;
    padding: 4px 10px;
    border-color: currentColor;
    opacity: 0.9;

    &:hover {
      opacity: 1;
      background: rgba(0, 0, 0, 0.08);
    }
  }

  .alert-dismiss {
    padding: 4px;
  }

  &.kind-quota_exceeded {
    color: var(--color-error-500);
    background: rgba(255, 77, 79, 0.08);
    border-color: rgba(255, 77, 79, 0.32);
  }

  &.kind-permission_denied {
    color: var(--color-warning-500);
    background: rgba(250, 173, 20, 0.08);
    border-color: rgba(250, 173, 20, 0.32);
  }

  &.kind-generic_error {
    color: var(--main-500);
    background: rgba(59, 130, 246, 0.08);
    border-color: rgba(59, 130, 246, 0.32);
  }
}
</style>
