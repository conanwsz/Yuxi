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
    <button
      v-if="dismissible"
      type="button"
      class="alert-dismiss"
      :aria-label="`关闭告警：${title}`"
      @click="emit('dismiss')"
    >
      <X :size="14" />
    </button>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { AlertTriangle, ShieldAlert, Info, X } from 'lucide-vue-next'

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
  dismissible: {
    type: Boolean,
    default: true
  }
})

const emit = defineEmits(['dismiss'])

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

  .alert-dismiss {
    flex-shrink: 0;
    background: transparent;
    border: none;
    padding: 2px;
    cursor: pointer;
    color: inherit;
    opacity: 0.6;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: 4px;
    transition: opacity 0.15s ease;

    &:hover {
      opacity: 1;
      background: rgba(0, 0, 0, 0.06);
    }
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
