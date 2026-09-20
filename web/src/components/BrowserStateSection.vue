<template>
  <section
    class="state-section browser-state-section"
    :class="{ 'is-collapsed': !isExpanded }"
    aria-label="浏览器"
  >
    <button
      type="button"
      class="state-section-header"
      :aria-expanded="isExpanded"
      aria-controls="state-section-browser"
      @click="$emit('toggle-expand')"
    >
      <span class="state-section-label">
        <span class="state-section-title">浏览器</span>
        <ChevronDown
          :size="15"
          class="state-section-chevron"
          :class="{ 'is-collapsed': !isExpanded }"
        />
      </span>
      <span v-if="statusLabel" class="browser-status-badge" :data-status="status">
        {{ statusLabel }}
      </span>
    </button>

    <div
      v-show="isExpanded"
      id="state-section-browser"
      class="state-section-content"
    >
      <!-- 16:9 浏览器截图 -->
      <div class="browser-screenshot-container">
        <img
          v-if="screenshot && status !== 'idle'"
          :src="`data:image/png;base64,${screenshot}`"
          alt="浏览器截图"
        />
        <div v-else class="browser-screenshot-empty">
          {{ status === 'idle' ? '浏览器处于空闲状态' : '等待浏览器工具被调用…' }}
        </div>
      </div>

      <!-- 页面快照（仅超级管理员可见，且默认折叠） -->
      <div v-if="userStore.isSuperAdmin && status !== 'idle'" class="browser-snapshot-section">
        <button
          type="button"
          class="browser-snapshot-header"
          :aria-expanded="isSnapshotExpanded"
          @click="isSnapshotExpanded = !isSnapshotExpanded"
        >
          <div class="browser-snapshot-header-left">
            <ChevronRight
              :size="13"
              class="browser-snapshot-chevron"
              :class="{ 'is-expanded': isSnapshotExpanded }"
            />
            <span class="browser-snapshot-title">页面快照 (DOM Snapshot)</span>
          </div>
          <span v-if="event?.url" class="browser-snapshot-url" :title="event.url">
            {{ event.url }}
          </span>
        </button>

        <div v-show="isSnapshotExpanded" class="browser-snapshot-body">
          <pre v-if="event?.snapshot" class="browser-snapshot-code"><code>{{ event.snapshot }}</code></pre>
          <div v-else class="browser-snapshot-empty">
            暂无页面快照数据
          </div>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup>
import { computed, ref } from 'vue'
import { ChevronDown, ChevronRight } from 'lucide-vue-next'
import { useUserStore } from '@/stores/user'

const props = defineProps({
  screenshot: { type: String, default: null },
  event: { type: Object, default: null },
  status: { type: String, default: 'idle' },
  isExpanded: { type: Boolean, default: true }
})

defineEmits(['toggle-expand'])

const userStore = useUserStore()
const isSnapshotExpanded = ref(false)

const statusLabel = computed(() => ({
  idle: '空闲',
  connecting: '连接中',
  active: '运行中',
  unavailable: '浏览器不可用',
  recovering: '重连中'
}[props.status] ?? props.status))
</script>

<style lang="less" scoped>
.state-section {
  display: flex;
  flex-direction: column;
  gap: 8px;

  &.is-collapsed {
    gap: 0;
  }
}

.state-section-header {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 2px 0;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: pointer;

  &:hover {
    .state-section-title,
    .state-section-chevron {
      color: var(--gray-900, #1a1a1a);
    }
  }

  &:focus-visible {
    outline: 2px solid var(--main-200, #bfdbfe);
    outline-offset: 2px;
  }
}

.state-section-label {
  min-width: 0;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.state-section-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--gray-800, #333);
}

.state-section-chevron {
  flex-shrink: 0;
  color: var(--gray-500, #888);
  transition: transform 0.18s ease, color 0.18s ease;

  &.is-collapsed {
    transform: rotate(-90deg);
  }
}

.state-section-content {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.browser-status-badge {
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 11px;
  line-height: 1.4;
  font-weight: 500;

  &[data-status="active"] { background: #e6f7e6; color: #389e0d; }
  &[data-status="connecting"] { background: #e6f7ff; color: #096dd9; }
  &[data-status="unavailable"] { background: #fff1f0; color: #cf1322; }
  &[data-status="recovering"] { background: #fffbe6; color: #d48806; }
  &[data-status="idle"] { background: var(--gray-100, #f0f0f0); color: var(--gray-600, #666); }
}

.browser-screenshot-container {
  width: 100%;
  aspect-ratio: 16 / 9;
  background: var(--gray-1000, #151616);
  border-radius: 6px;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;

  img {
    width: 100%;
    height: 100%;
    object-fit: contain;
    display: block;
  }
}

.browser-screenshot-empty {
  color: var(--gray-400, #999);
  font-size: 12px;
  text-align: center;
  padding: 16px;
}

.browser-snapshot-section {
  border: 1px solid var(--gray-100, #e5e7eb);
  border-radius: 6px;
  background: var(--gray-25, #f9fafb);
  overflow: hidden;
}

.browser-snapshot-header {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  padding: 6px 8px;
  border: none;
  background: var(--gray-50, #f3f4f6);
  cursor: pointer;
  text-align: left;
  transition: background-color 0.15s ease;

  &:hover {
    background: var(--gray-100, #e5e7eb);
  }
}

.browser-snapshot-header-left {
  display: flex;
  align-items: center;
  gap: 4px;
  min-width: 0;
}

.browser-snapshot-chevron {
  flex-shrink: 0;
  color: var(--gray-500, #6b7280);
  transition: transform 0.18s ease;

  &.is-expanded {
    transform: rotate(90deg);
  }
}

.browser-snapshot-title {
  font-size: 11px;
  font-weight: 600;
  color: var(--gray-700, #374151);
  white-space: nowrap;
}

.browser-snapshot-url {
  font-size: 10px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  color: var(--gray-500, #6b7280);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 160px;
}

.browser-snapshot-body {
  padding: 8px;
  max-height: 240px;
  overflow: auto;
  background: var(--gray-0, #fff);
  border-top: 1px solid var(--gray-100, #e5e7eb);
}

.browser-snapshot-code {
  margin: 0;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 11px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
  color: var(--gray-900, #111827);
}

.browser-snapshot-empty {
  font-size: 11px;
  color: var(--gray-400, #9ca3af);
  text-align: center;
  padding: 12px 0;
}
</style>
