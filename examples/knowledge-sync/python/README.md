# 超级楚楚知识库目录同步示例

此示例只使用 Python 3.11+ 标准库，把本地目录中的受支持文件同步到一个超级楚楚知识库。它演示当前 API 的正确调用顺序，不是需要安装到超级楚楚服务端的 SDK。

## 能力

- 使用 API Key 调用超级楚楚。
- 使用已有 `kb_id`，或按名称查找/创建知识库。
- 递归扫描目录并计算 SHA-256。
- 上传文件并提交 `auto_index=true` 任务。
- 轮询任务到终态。
- 用本地 JSON 保存 `relative_path -> content_hash/file_id`。
- 内容更新时先让新版本成功，再删除旧版本。
- 默认只报告来源中消失的文件；传 `--delete-removed` 才删除。
- 可选执行一次知识检索验证。

## 权限

API Key 关联用户至少需要：

```text
apikey.invoke
knowledge.read
knowledge.documents.manage
目标知识库 MANAGE 权限
system.tasks.manage
```

自动创建知识库还需要 `knowledge.create` 和可用的 Embedding 模型授权。

## 运行

```bash
export YUXI_BASE_URL='https://superchuchu.example.com'
export YUXI_API_KEY='yxkey_xxx'
export YUXI_KB_ID='kb_xxx'

python3 sync_knowledge.py ./source-documents \
  --state-file .yuxi-sync-state.json \
  --verify-query '设备 E-102 的额定压力是多少？'
```

使用部署方给出的 Embedding 模型标识自动建库：

```bash
unset YUXI_KB_ID
python3 sync_knowledge.py ./source-documents \
  --create-kb-name 'MES 设备知识库' \
  --embedding-model-spec 'provider/model'
```

删除来源目录中已经消失的文件：

```bash
python3 sync_knowledge.py ./source-documents --delete-removed
```

先预览计划，不发送任何写请求：

```bash
python3 sync_knowledge.py ./source-documents --dry-run
```

## 状态文件

默认状态文件是当前目录下的 `.yuxi-sync-state.json`：

```json
{
  "version": 1,
  "kb_id": "kb_xxx",
  "files": {
    "manuals/E-102.md": {
      "content_hash": "...",
      "file_id": "...",
      "synced_at": "2026-08-27T10:30:00+00:00"
    }
  }
}
```

生产环境应把它放在持久化存储并备份。不要让多个进程同时写同一个状态文件。如果新版本已经生效但旧版本删除失败，示例会在状态中暂存 `stale_file_ids` 并在下次运行重试清理。当前平台还没有第三方 `external_id` upsert，因此丢失状态文件后不能仅靠此示例无歧义地重建所有版本映射。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

测试不请求真实超级楚楚服务，只验证文件扫描、哈希、任务结果解析和状态持久化等客户端行为。
