export const RESOURCE_MODE_OPTIONS = [
  { label: '全部', value: 'all' },
  { label: '指定', value: 'selected' },
  { label: '无权限', value: 'none' }
]

export const RESOURCE_GROUP_CONFIG = {
  models: { title: '模型', supportsDefaults: true },
  tools: { title: '内置 Tool', supportsDefaults: false },
  mcp_servers: { title: 'MCP 服务', supportsDefaults: false }
}

export const MODEL_TYPES = ['chat', 'embedding', 'rerank']

const EMPTY_DEFAULTS = Object.freeze({
  chat: null,
  embedding: null,
  rerank: null
})

const uniqueStrings = (values) => [
  ...new Set(
    (Array.isArray(values) ? values : []).filter(
      (value) => typeof value === 'string' && value.trim()
    )
  )
]

const normalizeMode = (value, fallback = 'none') =>
  ['all', 'selected', 'none'].includes(value) ? value : fallback

const normalizeDefaults = (defaults = {}) =>
  MODEL_TYPES.reduce((acc, type) => {
    const value = defaults?.[type]
    acc[type] = typeof value === 'string' && value.trim() ? value.trim() : null
    return acc
  }, {})

export const createEmptyResourceAccess = () => ({
  models: { mode: 'none', allowed: [], defaults: { ...EMPTY_DEFAULTS } },
  tools: { mode: 'none', allowed: [] },
  mcp_servers: { mode: 'none', allowed: [] }
})

export const createAllResourceAccess = () => ({
  models: { mode: 'all', allowed: [], defaults: { ...EMPTY_DEFAULTS } },
  tools: { mode: 'all', allowed: [] },
  mcp_servers: { mode: 'all', allowed: [] }
})

export const cloneResourceAccess = (resourceAccess) =>
  JSON.parse(JSON.stringify(normalizeResourceAccess(resourceAccess)))

export function normalizeResourceAccess(resourceAccess, fallback = createAllResourceAccess()) {
  const normalizedFallback = fallback?.models ? fallback : createAllResourceAccess()
  return {
    models: {
      mode: normalizeMode(resourceAccess?.models?.mode, normalizedFallback.models.mode),
      allowed: uniqueStrings(resourceAccess?.models?.allowed || normalizedFallback.models.allowed),
      defaults: normalizeDefaults(
        resourceAccess?.models?.defaults || normalizedFallback.models.defaults
      )
    },
    tools: {
      mode: normalizeMode(resourceAccess?.tools?.mode, normalizedFallback.tools.mode),
      allowed: uniqueStrings(resourceAccess?.tools?.allowed || normalizedFallback.tools.allowed)
    },
    mcp_servers: {
      mode: normalizeMode(resourceAccess?.mcp_servers?.mode, normalizedFallback.mcp_servers.mode),
      allowed: uniqueStrings(
        resourceAccess?.mcp_servers?.allowed || normalizedFallback.mcp_servers.allowed
      )
    }
  }
}

const firstDefined = (...values) =>
  values.find((value) => value !== undefined && value !== null && value !== '')

const getNestedArray = (source, keys) => {
  for (const key of keys) {
    const value = source?.[key]
    if (Array.isArray(value)) return value
  }
  return []
}

const getItemKey = (item) =>
  String(
    firstDefined(
      item?.slug,
      item?.key,
      item?.name,
      item?.id,
      item?.server_name,
      item?.serverName,
      item?.tool_name,
      item?.toolName
    ) || ''
  ).trim()

const normalizeCatalogEntry = (item, fallbackLabel) => {
  const key = getItemKey(item)
  return {
    key,
    name: String(
      firstDefined(item?.name, item?.display_name, item?.title, key, fallbackLabel) || ''
    ),
    description: String(
      firstDefined(item?.description, item?.summary, item?.provider_name, '') || ''
    ),
    enabled: firstDefined(item?.enabled, item?.is_enabled, item?.active, true) !== false
  }
}

const normalizeModelEntry = (item) => {
  const spec = String(
    firstDefined(
      item?.spec,
      item?.model_spec,
      item?.id,
      item?.key,
      item?.provider_id && item?.model_id ? `${item.provider_id}:${item.model_id}` : null
    ) || ''
  ).trim()
  const [providerFromSpec = '', modelFromSpec = ''] = spec.split(':')
  const providerId = String(
    firstDefined(item?.provider_id, item?.providerId, providerFromSpec) || ''
  ).trim()
  const modelId = String(
    firstDefined(item?.model_id, item?.modelId, modelFromSpec, spec) || ''
  ).trim()
  const type = String(firstDefined(item?.model_type, item?.type, 'chat') || 'chat').trim()
  return {
    key: spec,
    spec,
    provider_id: providerId,
    provider_name: String(
      firstDefined(item?.provider_name, item?.providerName, providerId) || providerId
    ),
    model_id: modelId,
    name: String(firstDefined(item?.display_name, item?.name, modelId, spec) || spec),
    description: String(firstDefined(item?.description, item?.summary, '') || ''),
    type,
    enabled: firstDefined(item?.enabled, item?.is_enabled, item?.active, true) !== false
  }
}

export function normalizeResourceCatalog(rawCatalog = {}) {
  const models = getNestedArray(rawCatalog, ['models', 'model_catalog', 'modelCatalog'])
    .map(normalizeModelEntry)
    .filter((item) => item.spec)
  const tools = getNestedArray(rawCatalog, ['tools', 'tool_catalog', 'toolCatalog'])
    .map((item) => normalizeCatalogEntry(item, '未命名 Tool'))
    .filter((item) => item.key)
  const mcpServers = getNestedArray(rawCatalog, ['mcp_servers', 'mcpServers', 'mcp_server_catalog'])
    .map((item) => normalizeCatalogEntry(item, '未命名 MCP'))
    .filter((item) => item.key)
  return {
    models,
    tools,
    mcp_servers: mcpServers
  }
}
