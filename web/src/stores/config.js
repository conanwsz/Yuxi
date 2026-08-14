import { ref } from 'vue'
import { acceptHMRUpdate, defineStore } from 'pinia'
import { configApi } from '@/apis/system_api'

export const useConfigStore = defineStore('config', () => {
  const config = ref({})
  function setConfig(newConfig) {
    config.value = newConfig
  }

  /** 自动保存单个系统配置项，并在失败时恢复界面原值。 */
  async function setConfigValue(key, value) {
    const previousValue = config.value[key]
    config.value[key] = value

    try {
      const data = await configApi.updateConfigBatch({ [key]: value })
      setConfig(data)
      return true
    } catch {
      config.value[key] = previousValue
      return false
    }
  }

  async function refreshConfig() {
    const data = await configApi.getConfig()
    console.log('config', data)
    setConfig(data)
    return data
  }

  return { config, setConfigValue, refreshConfig }
})

if (import.meta.hot) {
  import.meta.hot.accept(acceptHMRUpdate(useConfigStore, import.meta.hot))
}
