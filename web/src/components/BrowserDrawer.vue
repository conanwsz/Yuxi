<template>
  <aside
    v-if="store.enabled"
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
        @click="store.hide()"
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
    <footer v-if="store.lastEvent?.snapshot" class="browser-drawer__footer">
      <code>{{ store.lastEvent.snapshot }}</code>
    </footer>
  </aside>
</template>

<script setup>
import { computed, onBeforeUnmount, watch } from 'vue'
import { useBrowserDrawerStore } from '@/stores/browserDrawer'
import { useBrowserStream } from '@/composables/useBrowserStream'

const props = defineProps({
  userId: { type: String, required: true }
})

const store = useBrowserDrawerStore()
const userIdRef = computed(() => props.userId)
const { events, status, connect, disconnect } = useBrowserStream(userIdRef)

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
  if (val.length) store.setLastEvent(val[val.length - 1])
}, { deep: true })

watch(status, (val) => {
  store.serviceStatus = val
  if (val === 'unavailable') store.bumpReconnect()
})

if (store.enabled && props.userId) {
  connect()
  store.show()
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
    flex: 1;
    overflow: auto;
    padding: 8px;

    img { max-width: 100%; display: block; }
  }

  &__empty {
    color: #999;
    padding: 24px;
    text-align: center;
  }

  &__footer {
    padding: 8px;
    border-top: 1px solid var(--border-color, #eee);
    font-size: 12px;
    max-height: 100px;
    overflow: auto;

    code {
      font-family: ui-monospace, monospace;
      white-space: pre-wrap;
    }
  }
}
</style>