export function buildDepartmentTreeSelectData(nodes = []) {
  if (!Array.isArray(nodes)) {
    return []
  }

  return nodes.map((node) => ({
    key: String(node.id),
    value: String(node.id),
    title: node.path_label || node.name || `部门 ${node.id}`,
    children: buildDepartmentTreeSelectData(node.children || [])
  }))
}

export function buildDepartmentDescendantMap(nodes = []) {
  const descendantMap = {}

  const visit = (node) => {
    const descendantIds = new Set([String(node.id)])

    for (const child of node.children || []) {
      for (const descendantId of visit(child)) {
        descendantIds.add(descendantId)
      }
    }

    descendantMap[String(node.id)] = descendantIds
    return descendantIds
  }

  for (const node of Array.isArray(nodes) ? nodes : []) {
    visit(node)
  }

  return descendantMap
}

export function expandDepartmentFilterIds(selectedDepartmentIds, nodes, includeDescendants = true) {
  const selections = Array.isArray(selectedDepartmentIds)
    ? selectedDepartmentIds
    : [selectedDepartmentIds]
  const selectedIds = selections
    .map((item) => (item && typeof item === 'object' ? item.value : item))
    .filter((item) => item !== null && typeof item !== 'undefined' && item !== '')
    .map(String)

  if (!includeDescendants) {
    return [...new Set(selectedIds)]
  }

  const descendantMap = buildDepartmentDescendantMap(nodes)
  const expandedIds = new Set(selectedIds)
  for (const departmentId of selectedIds) {
    for (const descendantId of descendantMap[departmentId] || []) {
      expandedIds.add(descendantId)
    }
  }
  return [...expandedIds]
}

export function collectUserDepartmentIds(user) {
  const departmentIds = new Set()
  const addDepartmentId = (departmentId) => {
    if (departmentId === null || typeof departmentId === 'undefined' || departmentId === '') return
    departmentIds.add(String(departmentId))
  }

  addDepartmentId(user?.primary_department?.department_id)
  addDepartmentId(user?.department_id)

  for (const membership of user?.part_time_departments || []) {
    addDepartmentId(membership?.department_id)
  }

  return [...departmentIds]
}

export function filterUserManagementUsers(users = [], filters = {}) {
  const keyword = String(filters.keyword || '')
    .trim()
    .toLowerCase()
  const role = String(filters.role || '').trim()
  // 兜底：v-model 偶发返回单值/字符串/label，统一规整成字符串 id 数组
  const rawSelection = filters.departmentIds
  const selectedIds = []
  if (Array.isArray(rawSelection)) {
    rawSelection.forEach((item) => {
      if (item === null || item === undefined || item === '') return
      if (typeof item === 'object' && 'value' in item) {
        selectedIds.push(String(item.value))
      } else {
        selectedIds.push(String(item))
      }
    })
  } else if (rawSelection !== null && rawSelection !== undefined && rawSelection !== '') {
    selectedIds.push(String(rawSelection))
  }
  const selectedSet = new Set(selectedIds)
  // label 兜底：v-model 可能把 label 当成 value 传过来（如 "默认部门"）；
  // 这里把所有非纯数字字符串视作 label 候选，纯数字字符串视作 id（已被 selectedSet 覆盖）。
  const selectedLabels = Array.isArray(rawSelection)
    ? rawSelection
        .map((item) => {
          if (item && typeof item === 'object' && 'label' in item) return String(item.label)
          if (typeof item === 'string' || typeof item === 'number') {
            const s = String(item)
            if (!/^\d+$/.test(s)) return s
          }
          return null
        })
        .filter(Boolean)
    : typeof rawSelection === 'string' && !/^\d+$/.test(rawSelection)
      ? [rawSelection]
      : []

  return (Array.isArray(users) ? users : []).filter((user) => {
    const matchesKeyword =
      !keyword ||
      [user.username, user.uid].some((value) =>
        String(value || '')
          .toLowerCase()
          .includes(keyword)
      )
    if (!matchesKeyword) {
      return false
    }

    if (role && user.role !== role) {
      return false
    }

    if (!selectedSet.size && !selectedLabels.length) {
      return true
    }

    const userDepartmentIds = collectUserDepartmentIds(user)
    if (userDepartmentIds.some((userDepartmentId) => selectedSet.has(userDepartmentId))) {
      return true
    }
    // label 兜底：v-model 返回 label 时（"默认部门"），按 department_name / department_path 命中
    if (selectedLabels.length) {
      const userLabels = [
        user.department_name,
        user.department_path,
        user.primary_department?.name,
        user.primary_department?.path_label,
        ...(user.part_time_departments || []).flatMap((m) => [m?.name, m?.path_label])
      ]
        .filter(Boolean)
        .map((v) => String(v))
      const userLabelSegments = userLabels.flatMap((label) => label.split(/\s*\/\s*/))
      if (selectedLabels.some((sl) => userLabelSegments.includes(sl) || userLabels.includes(sl))) {
        return true
      }
    }
    return false
  })
}
