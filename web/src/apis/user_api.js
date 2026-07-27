import { apiGet, apiPost } from './base'

export const userApi = {
  getTokenQuota: () => apiGet('/api/user/token-quota'),
  uploadImage: (file) => {
    const formData = new FormData()
    formData.append('file', file)
    return apiPost('/api/user/upload-image', formData)
  }
}
