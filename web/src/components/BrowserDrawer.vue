<template>
  <div>
    <!-- 展开抽屉 -->
    <aside
      v-if="store.enabled && store.visible"
      data-testid="browser-drawer"
      class="browser-drawer"
      :style="{ width: store.width + 'px' }"
    >
      <header class="browser-drawer__header">
        <span class="browser-drawer__title">浏览器视图</span>
        <span class="browser-drawer__status" :data-status="store.serviceStatus">
          {{ statusLabel }}
        </span>
        <button
          type="button"
          class="browser-drawer__close"
          aria-label="关闭浏览器视图"
          @click="handleManualClose"
        >×</button>
      </header>
      <div class="browser-drawer__body">
        <img
          v-if="latestScreenshot"
          :src="`data:image/png;base64,${latestScreenshot}`"
          alt="browser screenshot"
        />
        <div v-else class="browser-drawer__empty">
          等待浏览器工具被调用…
        </div>
      </div>
      <footer class="browser-drawer__footer">
        <div class="browser-drawer__footer-header">
          <span class="browser-drawer__footer-title">页面快照 (DOM Snapshot)</span>
          <span v-if="store.lastEvent?.url" class="browser-drawer__footer-url" :title="store.lastEvent.url">
            {{ store.lastEvent.url }}
          </span>
        </div>
        <div class="browser-drawer__footer-content">
          <code v-if="store.lastEvent?.snapshot">{{ store.lastEvent.snapshot }}</code>
          <div v-else class="browser-drawer__footer-empty">
            暂无页面快照数据
          </div>
        </div>
      </footer>
    </aside>

    <!-- 收起时的右侧边缘呼出按钮 -->
    <button
      v-else-if="store.enabled && !store.visible"
      type="button"
      class="browser-drawer__toggle-btn"
      title="展开浏览器视图"
      @click="handleManualOpen"
    >
      <Globe :size="16" />
      <span>浏览器视图</span>
    </button>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { Globe } from 'lucide-vue-next'
import { useBrowserDrawerStore } from '@/stores/browserDrawer'
import { useBrowserStream } from '@/composables/useBrowserStream'

const props = defineProps({
  userId: { type: [String, Number], required: true },
  threadId: { type: String, default: '' }
})

const store = useBrowserDrawerStore()
const userIdRef = computed(() => String(props.userId))
const { events, status, connect, disconnect } = useBrowserStream(userIdRef)

// 跟踪各会话是否曾自动打开过，以及用户是否手动关闭过
const autoOpenedThreads = ref(new Set())
const manuallyClosedThreads = ref(new Set())
const sessionKey = computed(() => props.threadId || '')

watch(() => props.threadId, (newId, oldId) => {
  if (!oldId && newId) {
    // 新会话首次发送消息后产生 threadId，平移记录
    if (manuallyClosedThreads.value.has('')) {
      manuallyClosedThreads.value.add(newId)
      manuallyClosedThreads.value.delete('')
    }
    if (autoOpenedThreads.value.has('')) {
      autoOpenedThreads.value.add(newId)
      autoOpenedThreads.value.delete('')
    }
  }
})

const handleManualClose = () => {
  manuallyClosedThreads.value.add(sessionKey.value)
  store.hide()
}

const handleManualOpen = () => {
  manuallyClosedThreads.value.delete(sessionKey.value)
  store.show()
}

const isBrowserUsed = (event) => {
  if (!event) return false
  if (event.url && event.url !== 'about:blank') return true
  if (event.action_summary) return true
  if (event.snapshot && event.snapshot.trim() && !event.snapshot.includes('Page URL: about:blank')) return true
  return false
}

const latestScreenshot = computed(() => {
  for (let i = events.value.length - 1; i >= 0; i--) {
    if (events.value[i]?.screenshot_b64) return events.value[i].screenshot_b64
  }
  return null
})

const statusLabel = computed(() => ({
  idle: '空闲',
  connecting: '连接中',
  active: '运行中',
  unavailable: '浏览器不可用',
  recovering: '重连中'
}[store.serviceStatus] ?? store.serviceStatus))

watch(events, (val) => {
  if (val.length) {
    const latest = val[val.length - 1]
    store.setLastEvent(latest)

    const key = sessionKey.value
    // 每个会话第一次使用到浏览器时，自动打开浏览器视图；若用户已手动关闭，则尊重用户意愿不再强行打开
    if (isBrowserUsed(latest) && !autoOpenedThreads.value.has(key)) {
      autoOpenedThreads.value.add(key)
      if (!manuallyClosedThreads.value.has(key) && store.enabled) {
        store.show()
      }
    }
  }
}, { deep: true })

watch(status, (val) => {
  store.serviceStatus = val
  if (val === 'unavailable') store.bumpReconnect()
})

if (store.enabled && props.userId) {
  connect()
}

onBeforeUnmount(() => {
  disconnect()
})
</script>

<style scoped lang="less">
.browser-drawer {
  position: fixed;
  top: 0;
  right: 0;
  height: 100vh;
  background: var(--background-primary, #fff);
  border-left: 1px solid var(--border-color, #ddd);
  display: flex;
  flex-direction: column;
  z-index: 100;
  box-shadow: -2px 0 8px rgba(0, 0, 0, 0.08);

  &__header {
    display: flex;
    align-items: center;
    padding: 8px 12px;
    border-bottom: 1px solid var(--border-color, #eee);
  }

  &__title { font-weight: 600; }

  &__status {
    margin-left: auto;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 12px;

    &[data-status="active"] { background: #e6f7e6; color: #389e0d; }
    &[data-status="connecting"] { background: #e6f7ff; color: #096dd9; }
    &[data-status="unavailable"] { background: #fff1f0; color: #cf1322; }
    &[data-status="recovering"] { background: #fffbe6; color: #d48806; }
  }

  &__close {
    margin-left: 12px;
    cursor: pointer;
    background: none;
    border: 0;
    font-size: 18px;
    line-height: 1;
    color: #666;

    &:hover { color: #000; }
  }

  &__body {
    width: 100%;
    aspect-ratio: 16 / 9;
    flex-shrink: 0;
    background: var(--gray-1000, #151616);
    display: flex;
    align-items: center;
    justify-content: center;
    overflow: hidden;
    border-bottom: 1px solid var(--border-color, #eee);

    img {
      width: 100%;
      height: 100%;
      object-fit: contain;
      display: block;
    }
  }

  &__empty {
    color: var(--gray-400, #999);
    padding: 24px;
    font-size: 13px;
    text-align: center;
  }

  &__footer {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
    background: var(--gray-25, #f8fafa);
    overflow: hidden;
  }

  &__footer-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 6px 12px;
    background: var(--gray-50, #f5f7f7);
    border-bottom: 1px solid var(--border-color, #eee);
    font-size: 12px;
    gap: 8px;
    flex-shrink: 0;
  }

  &__footer-title {
    font-weight: 600;
    color: var(--gray-800, #323333);
    white-space: nowrap;
  }

  &__footer-url {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: var(--gray-500, #979999);
    font-size: 11px;
    font-family: ui-monospace, monospace;
  }

  &__footer-content {
    flex: 1;
    min-height: 0;
    overflow: auto;
    padding: 10px 12px;

    code {
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 12px;
      line-height: 1.5;
      white-space: pre-wrap;
      word-break: break-all;
      color: var(--gray-900, #1e1f1f);
    }
  }

  &__footer-empty {
    color: var(--gray-400, #999);
    font-size: 12px;
    text-align: center;
    padding: 24px 0;
  }

  &__toggle-btn {
    position: fixed;
    right: 0;
    top: 120px;
    z-index: 99;
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 8px 12px;
    background: var(--background-primary, #fff);
    border: 1px solid var(--border-color, #d9d9d9);
    border-right: none;
    border-radius: 8px 0 0 8px;
    box-shadow: -2px 2px 8px rgba(0, 0, 0, 0.08);
    cursor: pointer;
    font-size: 13px;
    color: var(--text-color, #333);
    transition: all 0.2s ease;

    &:hover {
      background: var(--background-secondary, #fafafa);
      color: var(--primary-color, #1677ff);
    }
  }
}
</style>