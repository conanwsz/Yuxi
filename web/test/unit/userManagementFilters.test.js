import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildDepartmentTreeSelectData,
  collectUserDepartmentIds,
  expandDepartmentFilterIds,
  filterUserManagementUsers
} from '../../src/utils/userManagementFilters.js'

const departmentTree = [
  {
    id: 1,
    name: '总部',
    path_label: '总部',
    children: [
      {
        id: 2,
        name: '研发中心',
        path_label: '总部 / 研发中心',
        children: [
          {
            id: 3,
            name: '平台组',
            path_label: '总部 / 研发中心 / 平台组',
            children: []
          }
        ]
      }
    ]
  }
]

const users = [
  {
    id: 1,
    username: 'alice',
    uid: 'alice',
    role: 'user',
    department_id: 2,
    department_name: '研发中心',
    department_path: '总部 / 研发中心',
    primary_department: { department_id: 2, name: '研发中心', path_label: '总部 / 研发中心' },
    part_time_departments: []
  },
  {
    id: 2,
    username: 'bob',
    uid: 'bob',
    role: 'admin',
    department_id: 9,
    department_name: '未分配',
    department_path: '未分配',
    primary_department: { department_id: 9, name: '未分配', path_label: '未分配' },
    part_time_departments: [
      { department_id: 3, name: '平台组', path_label: '总部 / 研发中心 / 平台组' }
    ]
  },
  {
    id: 3,
    username: 'charlie',
    uid: 'charlie',
    role: 'user',
    department_id: 9,
    department_name: '未分配',
    department_path: '未分配',
    primary_department: { department_id: 9, name: '未分配', path_label: '未分配' },
    part_time_departments: []
  }
]

test('buildDepartmentTreeSelectData 保留树结构并输出 TreeSelect 所需字段', () => {
  assert.deepEqual(buildDepartmentTreeSelectData(departmentTree), [
    {
      key: '1',
      value: '1',
      title: '总部',
      children: [
        {
          key: '2',
          value: '2',
          title: '总部 / 研发中心',
          children: [
            {
              key: '3',
              value: '3',
              title: '总部 / 研发中心 / 平台组',
              children: []
            }
          ]
        }
      ]
    }
  ])
})

test('collectUserDepartmentIds 同时收集主部门和兼职部门', () => {
  assert.deepEqual(collectUserDepartmentIds(users[1]), ['9', '3'])
})

test('expandDepartmentFilterIds 默认包含所选部门的全部下级', () => {
  assert.deepEqual(expandDepartmentFilterIds(['2'], departmentTree), ['2', '3'])
})

test('expandDepartmentFilterIds 仅直属模式不展开下级', () => {
  assert.deepEqual(expandDepartmentFilterIds(['2'], departmentTree, false), ['2'])
})

test('filterUserManagementUsers 空 departmentIds 不应用部门筛选', () => {
  const result = filterUserManagementUsers(users, { departmentIds: [] })
  assert.deepEqual(
    result.map((user) => user.username),
    ['alice', 'bob', 'charlie']
  )
})

test('filterUserManagementUsers 单部门命中主部门和兼职部门', () => {
  const result = filterUserManagementUsers(users, { departmentIds: ['9'] })
  assert.deepEqual(
    result.map((user) => user.username),
    ['bob', 'charlie']
  )
})

test('filterUserManagementUsers 兼职部门单选命中', () => {
  const result = filterUserManagementUsers(users, { departmentIds: ['3'] })
  assert.deepEqual(
    result.map((user) => user.username),
    ['bob']
  )
})

test('filterUserManagementUsers 多部门 OR 命中', () => {
  const result = filterUserManagementUsers(users, { departmentIds: ['2', '3'] })
  assert.deepEqual(
    result.map((user) => user.username),
    ['alice', 'bob']
  )
})

test('filterUserManagementUsers 单值入参被规整为单选命中', () => {
  const result = filterUserManagementUsers(users, { departmentIds: '2' })
  assert.deepEqual(
    result.map((user) => user.username),
    ['alice']
  )
})

test('filterUserManagementUsers 数字入参按字符串匹配', () => {
  const result = filterUserManagementUsers(users, { departmentIds: 9 })
  assert.deepEqual(
    result.map((user) => user.username),
    ['bob', 'charlie']
  )
})

test('filterUserManagementUsers LabeledValue 入参取 value 字段', () => {
  const result = filterUserManagementUsers(users, {
    departmentIds: [
      { value: '3', label: '平台组' },
      { value: '2', label: '研发中心' }
    ]
  })
  assert.deepEqual(
    result.map((user) => user.username),
    ['alice', 'bob']
  )
})

test('filterUserManagementUsers label 兜底命中（无 id 匹配时按 name/path 匹配）', () => {
  const result = filterUserManagementUsers(users, { departmentIds: ['平台组'] })
  assert.deepEqual(
    result.map((user) => user.username),
    ['bob']
  )
})

test('filterUserManagementUsers 同时应用关键字和角色筛选', () => {
  const result = filterUserManagementUsers(users, {
    keyword: 'bo',
    role: 'admin'
  })

  assert.deepEqual(
    result.map((user) => user.username),
    ['bob']
  )
})
