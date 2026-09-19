import { defineStore } from 'pinia'

export const useBrowserDrawerStore = defineStore('browserDrawer', {
  state: () => ({
    visible: false,
    enabled: true,           // 用户可永久关闭
    width: 480,
    lastEvent: null,
    reconnectCount: 0,
    serviceStatus: 'idle',   // idle | active | unavailable | recovering
  }),
  actions: {
    show() { this.visible = true },
    hide() { this.visible = false },
    toggle() { this.visible = !this.visible },
    setEnabled(v) {
      this.enabled = !!v
      if (!v) this.hide()
    },
    setWidth(w) { this.width = Math.max(320, Math.min(720, w)) },
    setLastEvent(ev) {
      this.lastEvent = ev
      if (ev && ev.status) this.serviceStatus = ev.status
    },
    bumpReconnect() { this.reconnectCount++ }
  },
  persist: {
    paths: ['enabled', 'width']
  }
})