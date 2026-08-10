/**
 * 将失败的远程 Skill 按来源重新组织为上游安装请求。
 *
 * 平台为避免本地 slug 冲突可能生成 `-v2` 后缀，但远程 CLI 只识别原始名称。
 */
export function buildRemoteSkillRetryRequests(items) {
  const requestsBySource = new Map()

  items.forEach((item) => {
    if (item.source_type !== 'remote' || !item.source) return
    if (!requestsBySource.has(item.source)) requestsBySource.set(item.source, [])
    requestsBySource.get(item.source).push(item.original_name || item.slug)
  })

  return [...requestsBySource].map(([source, skills]) => ({ source, skills }))
}
