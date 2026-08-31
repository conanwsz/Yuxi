/**
 * 根据当前用户身份与权限返回可访问的设置页签。
 *
 * 这里同时作为导航展示和组件挂载的权限来源，避免隐藏页签仍请求受限接口。
 */
export function getAvailableSettingsTabs(userStore) {
  const tabs = []

  if (userStore.isLoggedIn) tabs.push('account')
  if (userStore.hasPermission('apikey.manage')) tabs.push('apiKeys')
  if (userStore.isLoggedIn) tabs.push('agentEnv')
  if (userStore.hasPermission('system.config.update')) tabs.push('base')
  if (['admin', 'superadmin'].includes(userStore.userRole)) tabs.push('ocr')
  if (userStore.hasPermission('users.read')) tabs.push('user')
  if (userStore.hasPermission('departments.read')) tabs.push('department')
  if (userStore.isSuperAdmin) tabs.push('permission')

  return tabs
}
