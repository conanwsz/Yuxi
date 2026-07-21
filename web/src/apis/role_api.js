import { apiDelete, apiGet, apiPost, apiPut } from './base'

const BASE_URL = '/api/roles'

export const roleApi = {
  getPermissionCatalog: () => apiGet(`${BASE_URL}/permissions`),
  getResourceCatalog: () => apiGet(`${BASE_URL}/resources`),
  getRoles: () => apiGet(BASE_URL),
  createRole: (data) => apiPost(BASE_URL, data),
  updateRole: (key, data) => apiPut(`${BASE_URL}/${encodeURIComponent(key)}`, data),
  deleteRole: (key) => apiDelete(`${BASE_URL}/${encodeURIComponent(key)}`)
}
