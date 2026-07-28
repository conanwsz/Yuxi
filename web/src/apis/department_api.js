/**
 * 部门管理 API
 */

import { apiAdminGet, apiAdminPost, apiAdminPut, apiSuperAdminDelete } from './base'

const BASE_URL = '/api/departments'

/**
 * 获取部门列表（普通管理员可访问）
 * @returns {Promise<Array>} 部门列表
 */
export const getDepartments = () => {
  return apiAdminGet(BASE_URL)
}

export const getDepartmentTree = () => {
  return apiAdminGet(`${BASE_URL}/tree`)
}

/**
 * 获取部门详情
 * @param {number} departmentId - 部门ID
 * @returns {Promise<Object>} 部门详情
 */
export const getDepartment = (departmentId) => {
  return apiAdminGet(`${BASE_URL}/${departmentId}`)
}

/**
 * 创建部门
 * @param {Object} data - 部门数据
 * @param {string} data.name - 部门名称
 * @param {string} [data.description] - 部门描述
 * @returns {Promise<Object>} 创建的部门
 */
export const createDepartment = (data) => {
  return apiAdminPost(BASE_URL, data)
}

/**
 * 更新部门
 * @param {number} departmentId - 部门ID
 * @param {Object} data - 部门数据
 * @param {string} [data.name] - 部门名称
 * @param {string} [data.description] - 部门描述
 * @returns {Promise<Object>} 更新后的部门
 */
export const updateDepartment = (departmentId, data) => {
  return apiAdminPut(`${BASE_URL}/${departmentId}`, data)
}

export const moveDepartment = (departmentId, parentId) => {
  return apiAdminPost(`${BASE_URL}/${departmentId}/move`, { parent_id: parentId })
}

export const archiveDepartment = (departmentId) => {
  return apiAdminPost(`${BASE_URL}/${departmentId}/archive`)
}

export const restoreDepartment = (departmentId) => {
  return apiAdminPost(`${BASE_URL}/${departmentId}/restore`)
}

/**
 * 删除部门
 * @param {number} departmentId - 部门ID
 * @returns {Promise<Object>} 删除结果
 */
export const deleteDepartment = (departmentId) => {
  return apiSuperAdminDelete(`${BASE_URL}/${departmentId}`)
}

export const departmentApi = {
  getDepartments,
  getDepartmentTree,
  getDepartment,
  createDepartment,
  updateDepartment,
  moveDepartment,
  archiveDepartment,
  restoreDepartment,
  deleteDepartment
}
