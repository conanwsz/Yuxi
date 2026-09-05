import {
  apiGet,
  apiPost,
  apiPut,
  apiDelete,
  apiAdminGet,
  apiAdminPost,
  apiAdminPut,
  apiAdminPatch,
  apiAdminDelete
} from './base'

const BASE_URL = '/api/system/skills'
const USER_BASE_URL = '/api/skills'

export const listSkills = async () => {
  return apiGet(BASE_URL)
}

export const listSkillCards = async ({ refreshPersonal = false } = {}) => {
  const query = refreshPersonal ? '?refresh_personal=true' : ''
  return apiGet(`${USER_BASE_URL}${query}`)
}

export const listAccessibleSkills = async () => {
  return apiGet(`${USER_BASE_URL}/accessible`)
}

export const prepareSkillUpload = async (file) => {
  const formData = new FormData()
  formData.append('file', file)
  return apiPost(`${USER_BASE_URL}/import/prepare`, formData)
}

export const listRemoteSkills = async (source) => {
  return apiPost(`${USER_BASE_URL}/remote/list`, { source })
}

export const prepareRemoteSkills = async (payload) => {
  return apiPost(`${USER_BASE_URL}/remote/prepare`, payload)
}

export const searchRemoteSkills = async (query) => {
  return apiPost(`${USER_BASE_URL}/remote/search`, { query })
}

export const confirmSkillInstallDraft = async (draftId, shareConfig, slugs) => {
  return apiPost(`${USER_BASE_URL}/install-drafts/${encodeURIComponent(draftId)}/confirm`, {
    share_config: shareConfig,
    slugs
  })
}

export const confirmPersonalSkillInstallDraft = async (draftId, slugs) => {
  return apiPost(
    `${USER_BASE_URL}/personal/install-drafts/${encodeURIComponent(draftId)}/confirm`,
    {
      slugs
    }
  )
}

export const discardSkillInstallDraft = async (draftId) => {
  return apiDelete(`${USER_BASE_URL}/install-drafts/${encodeURIComponent(draftId)}`)
}

export const getSkillDependencyOptions = async (slug) => {
  const query = slug ? `?slug=${encodeURIComponent(slug)}` : ''
  return apiGet(`${BASE_URL}/dependency-options${query}`)
}

export const listBuiltinSkills = async () => {
  return apiAdminGet(`${BASE_URL}/builtin`)
}

export const syncBuiltinSkills = async () => {
  return apiAdminPost(`${BASE_URL}/builtin/sync`)
}

export const getSkillTree = async (slug) => {
  return apiGet(`${BASE_URL}/${encodeURIComponent(slug)}/tree`)
}

export const getSkillFile = async (slug, path) => {
  return apiGet(`${BASE_URL}/${encodeURIComponent(slug)}/file?path=${encodeURIComponent(path)}`)
}

export const getPersonalSkillFile = async (slug, path) => {
  return apiGet(
    `${USER_BASE_URL}/personal/${encodeURIComponent(slug)}/file?path=${encodeURIComponent(path)}`
  )
}

export const createSkillFile = async (slug, payload) => {
  return apiPost(`${BASE_URL}/${encodeURIComponent(slug)}/file`, payload)
}

export const updateSkillFile = async (slug, payload) => {
  return apiPut(`${BASE_URL}/${encodeURIComponent(slug)}/file`, payload)
}

export const updateSkillDependencies = async (slug, payload) => {
  return apiPut(`${BASE_URL}/${encodeURIComponent(slug)}/dependencies`, payload)
}

export const updateSkillShareConfig = async (slug, shareConfig) => {
  return apiPut(`${BASE_URL}/${encodeURIComponent(slug)}/share-config`, {
    share_config: shareConfig
  })
}

export const updateSkillEnabled = async (slug, enabled) => {
  return apiPut(`${BASE_URL}/${encodeURIComponent(slug)}/enabled`, { enabled })
}

export const deleteSkillFile = async (slug, path) => {
  return apiDelete(`${BASE_URL}/${encodeURIComponent(slug)}/file?path=${encodeURIComponent(path)}`)
}

export const exportSkill = async (slug) => {
  return apiGet(`${BASE_URL}/${encodeURIComponent(slug)}/export`, {}, true, 'blob')
}

export const deleteSkill = async (slug) => {
  return apiDelete(`${BASE_URL}/${encodeURIComponent(slug)}`)
}

export const deletePersonalSkill = async (slug) => {
  return apiDelete(`${USER_BASE_URL}/personal/${encodeURIComponent(slug)}`)
}

export const deleteSkillsBatch = async (slugs) => {
  return apiPost(`${BASE_URL}/delete-batch`, { slugs })
}

// ---------- 推荐技能套件 ----------
// 公共视角：仅返回启用的套件（任意有 skills.read 权限的用户可访问）
export const listRecommendedSuites = async () => {
  return apiGet(`${BASE_URL}/recommended-suites`)
}

// 管理视角：含 disabled
export const listRecommendedSuitesAdmin = async () => {
  return apiAdminGet(`${BASE_URL}/recommended-suites/admin`)
}

export const getRecommendedSuite = async (id) => {
  return apiGet(`${BASE_URL}/recommended-suites/${id}`)
}

export const createRecommendedSuite = async (payload) => {
  return apiAdminPost(`${BASE_URL}/recommended-suites`, payload)
}

export const updateRecommendedSuite = async (id, payload) => {
  return apiAdminPut(`${BASE_URL}/recommended-suites/${id}`, payload)
}

export const setRecommendedSuiteEnabled = async (id, enabled) => {
  return apiAdminPatch(`${BASE_URL}/recommended-suites/${id}/enabled`, { enabled })
}

export const deleteRecommendedSuite = async (id) => {
  return apiAdminDelete(`${BASE_URL}/recommended-suites/${id}`)
}

// 套件上传：解析多 skill 的 zip，返回每个 skill 的元数据（不持久化草稿）
export const prepareSuiteUpload = async (file) => {
  const formData = new FormData()
  formData.append('file', file)
  return apiAdminPost(`${USER_BASE_URL}/import/suite-prepare`, formData)
}

// ---------- 推荐工作区（用户共建池） ----------
// 列出当前用户可访问的「推荐工作区」skill
export const listRecommendedWorkspace = async () => {
  return apiGet(`${USER_BASE_URL}/recommended-workspace`)
}

// 把 draft 里的 skill 直接装入「推荐工作区」（不开新草稿，1 步完成）
export const confirmRecommendedWorkspaceInstall = async (draftId, slugs) => {
  return apiPost(`${USER_BASE_URL}/import/install-to-recommended-workspace`, {
    draft_id: draftId,
    slugs
  })
}

// 从推荐工作区克隆一个 skill 到当前用户的个人工作区
export const installRecommendedWorkspaceToPersonal = async (slug) => {
  return apiPost(
    `${USER_BASE_URL}/recommended-workspace/${encodeURIComponent(slug)}/install-to-personal`
  )
}

export const skillApi = {
  listSkills,
  listSkillCards,
  listAccessibleSkills,
  prepareSkillUpload,
  listRemoteSkills,
  prepareRemoteSkills,
  searchRemoteSkills,
  confirmSkillInstallDraft,
  confirmPersonalSkillInstallDraft,
  discardSkillInstallDraft,
  getSkillDependencyOptions,
  listBuiltinSkills,
  syncBuiltinSkills,
  getSkillTree,
  getSkillFile,
  getPersonalSkillFile,
  createSkillFile,
  updateSkillFile,
  updateSkillDependencies,
  updateSkillShareConfig,
  updateSkillEnabled,
  deleteSkillFile,
  exportSkill,
  deleteSkill,
  deletePersonalSkill,
  deleteSkillsBatch,
  listRecommendedSuites,
  listRecommendedSuitesAdmin,
  getRecommendedSuite,
  createRecommendedSuite,
  updateRecommendedSuite,
  setRecommendedSuiteEnabled,
  deleteRecommendedSuite,
  prepareSuiteUpload,
  listRecommendedWorkspace,
  confirmRecommendedWorkspaceInstall,
  installRecommendedWorkspaceToPersonal
}

export default skillApi
