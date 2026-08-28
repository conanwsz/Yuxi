# 知识库开放接口对接指南

本文面向需要从业务系统自动抽取数据，并在超级楚楚中建立或持续更新知识库的开发人员。文档按实际同步流程组织；单个接口的字段定义可同时参考后端 Swagger UI。

> 当前知识同步接口适用于受控的内部系统集成。它尚未提供版本化的 `/api/v1` 集成协议、API Key 独立知识库白名单、Webhook 或 `external_id` 原生幂等更新。接入方应阅读[当前边界](#当前边界)并保存自己的同步状态。

## 交付物

- 本指南：端到端流程、调用顺序、错误处理和验收标准。
- Swagger UI：`{base_url}/docs`，用于查看字段并在线调试。
- [Postman 集合](/downloads/knowledge-sync/super-chuchu-knowledge-sync.postman_collection.json)：按编号执行即可跑通一次同步。
- Python 示例工程：仓库 `examples/knowledge-sync/python`，支持目录首次同步、安全更新、可选删除和检索验证。

## 1. 十五分钟快速接入

### 1.1 准备连接信息

向超级楚楚管理员取得：

```text
base_url = https://superchuchu.example.com
api_key  = yxkey_...
kb_id    = kb_...
```

生产环境必须使用 HTTPS。API Key 通过请求头传递：

```http
Authorization: Bearer yxkey_xxxxxxxxx
```

API Key 绑定到一个真实用户并继承该用户的权限。用于知识同步的身份需要：

| 权限或条件 | 用途 |
| --- | --- |
| 已绑定部门 | 所有受保护 API 的基础条件 |
| `apikey.invoke` | 允许使用 API Key 调用 |
| `knowledge.read` | 查看知识库和同步结果 |
| `knowledge.documents.manage` | 上传、解析、入库、删除文档 |
| 目标知识库 `MANAGE` 资源权限 | 管理指定知识库中的文件 |
| `system.tasks.manage` | 通过 `task_id` 查询后台任务，推荐授予 |
| `knowledge.create` | 仅在集成程序需要自动创建知识库时授予 |

普通 `user` 角色对知识库的资源权限上限是 `READ`，不能执行文档同步。应创建专用、权限尽量小的集成账号，不要复用日常超级管理员账号。

### 1.2 验证认证与知识库

```bash
curl --fail-with-body \
  -H 'Authorization: Bearer yxkey_xxxxxxxxx' \
  'https://superchuchu.example.com/api/knowledge/databases'
```

成功响应包含当前身份可访问的 `databases`。确认目标 `kb_id` 存在，并且该知识库类型支持文档。

### 1.3 上传原始文件

```bash
curl --fail-with-body -X POST \
  -H 'Authorization: Bearer yxkey_xxxxxxxxx' \
  -F 'file=@./设备维护手册.pdf' \
  'https://superchuchu.example.com/api/knowledge/files/upload?kb_id=kb_example'
```

保存响应中的三个字段：

```json
{
  "file_path": "minio://knowledgebases/kb_example/upload/manual_1720000000000.pdf",
  "content_hash": "文件内容哈希",
  "size": 1048576
}
```

上传成功只表示原始文件已写入对象存储，**此时知识仍不可检索**。

### 1.4 提交解析和自动入库

```bash
curl --fail-with-body -X POST \
  -H 'Authorization: Bearer yxkey_xxxxxxxxx' \
  -H 'Content-Type: application/json' \
  -d '{
    "items": [
      "minio://knowledgebases/kb_example/upload/manual_1720000000000.pdf"
    ],
    "params": {
      "content_type": "file",
      "content_hashes": {
        "minio://knowledgebases/kb_example/upload/manual_1720000000000.pdf": "文件内容哈希"
      },
      "file_sizes": {
        "minio://knowledgebases/kb_example/upload/manual_1720000000000.pdf": 1048576
      },
      "auto_index": true
    }
  }' \
  'https://superchuchu.example.com/api/knowledge/databases/kb_example/documents'
```

响应为异步任务：

```json
{
  "message": "任务已提交，请在任务中心查看进度",
  "status": "queued",
  "task_id": "1a2b3c..."
}
```

### 1.5 等待任务终态

```bash
curl --fail-with-body \
  -H 'Authorization: Bearer yxkey_xxxxxxxxx' \
  'https://superchuchu.example.com/api/tasks/1a2b3c...'
```

按 `task.status` 处理：

| 状态 | 客户端动作 |
| --- | --- |
| `pending` | 等待后继续轮询 |
| `running` | 记录 `progress/message`，继续轮询 |
| `success` | 保存 `result.items[].file_id`，进入检索验证 |
| `failed` | 保存 `error` 和 `result`，按错误类型决定重试 |
| `cancelled` | 停止轮询并记录人工或系统取消原因 |

如果集成身份没有 `system.tasks.manage`，可轮询 `GET /api/knowledge/databases/{kb_id}/documents`，但无法取得完整任务错误和进度，因此不推荐作为正式接入方案。

## 2. 系统与数据流程

### 2.1 首次同步流程

![第三方系统首次同步知识库流程](/diagrams/knowledge-sync/first-sync-flow.svg)

### 2.2 单文件时序

![单个知识文件同步时序](/diagrams/knowledge-sync/file-sequence.svg)

### 2.3 安全更新顺序

当前平台没有按第三方业务主键执行原子 upsert 的接口。更新已有知识文件时，必须先让新版本成功，再删除旧版本：

![知识文件安全更新流程](/diagrams/knowledge-sync/safe-update-flow.svg)

禁止使用“先删旧文件、再上传新文件”的顺序，否则新版本解析或向量入库失败时，线上知识会出现空窗。

## 3. 业务数据如何转换

超级楚楚当前接收文件，不直接接收任意业务表记录。第三方系统应先把来源数据转换成稳定、可读的知识文件：

| 来源 | 推荐格式 | 建议 |
| --- | --- | --- |
| 业务数据库记录 | Markdown 或 CSV | Markdown 适合一条记录一篇知识；CSV 适合同构批量记录 |
| 富文本页面 | HTML 或 Markdown | 保留标题层级，去掉导航、脚本和无关样式 |
| 制度、手册 | PDF、DOCX、PPTX | 保留原文件；扫描件和图片需要启用 OCR 配置 |
| 接口 JSON | JSON 或转换后的 Markdown | 删除密钥、个人敏感信息和纯技术字段 |
| 图片资料 | PNG/JPG/TIFF | 必须选择有效 OCR 引擎，否则无法产生可检索文本 |

建议在文件正文或同步状态中保存以下来源元数据：

```json
{
  "source_system": "MES",
  "source_type": "equipment_manual",
  "external_id": "MANUAL-2026-00125",
  "source_version": "17",
  "source_updated_at": "2026-08-27T10:30:00+08:00"
}
```

这些字段目前不会被超级楚楚自动解释为 upsert 主键，但能帮助审计、排错，并为未来迁移到正式集成 API 做准备。

## 4. 核心接口清单

### 4.1 API Key 管理

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/api/user/apikey/` | 创建 API Key，完整 `secret` 仅返回一次 |
| `GET` | `/api/user/apikey/` | 查看当前身份可管理的 Key |
| `PUT` | `/api/user/apikey/{api_key_id}` | 修改名称、有效期、启用状态 |
| `DELETE` | `/api/user/apikey/{api_key_id}` | 删除 Key |

API Key 创建属于管理操作，通常由管理员在超级楚楚界面完成，不应写进日常同步任务。

### 4.2 知识库管理

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/api/knowledge/databases` | 列出可访问知识库并取得 `kb_id` |
| `POST` | `/api/knowledge/databases` | 可选：自动创建默认 Milvus 知识库 |
| `GET` | `/api/knowledge/databases/{kb_id}` | 查询知识库详情 |

创建默认知识库请求：

```json
{
  "database_name": "MES 设备知识库",
  "description": "由 MES 知识同步任务维护",
  "embedding_model_spec": "部署方提供的 embedding 模型标识",
  "kb_type": "milvus"
}
```

模型标识必须是集成用户获准使用的 Embedding 模型。生产接入更推荐由管理员提前建库并只向三方提供固定 `kb_id`。

### 4.3 文件同步

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/api/knowledge/files/supported-types` | 查询当前支持的扩展名 |
| `POST` | `/api/knowledge/files/upload?kb_id=...` | 上传一个原始文件，不解析、不入库 |
| `POST` | `/api/knowledge/databases/{kb_id}/documents` | 添加、解析，并可选自动向量入库 |
| `GET` | `/api/tasks/{task_id}` | 查询处理任务 |
| `GET` | `/api/knowledge/databases/{kb_id}/documents` | 分页查询文档与状态 |
| `GET` | `/api/knowledge/databases/{kb_id}/documents/exists` | 按文件名/路径检查存在性 |
| `DELETE` | `/api/knowledge/databases/{kb_id}/documents/{doc_id}` | 删除旧版或失效文档 |
| `POST` | `/api/knowledge/databases/{kb_id}/query` | 检索验证 |

当前单文件上限为 100 MB。支持类型以 `supported-types` 接口为准，当前包括：

```text
txt md docx html htm json csv xls xlsx pdf pptx
jpg jpeg png bmp tiff tif zip
```

## 5. 全量、增量、更新与删除

### 5.1 首次全量同步

1. 固定一次同步快照时间，避免边抽取边变化。
2. 为每条来源记录生成稳定的相对路径或文件名。
3. 计算本地 SHA-256。
4. 逐文件上传并提交 `auto_index=true`。
5. 等待每个任务终态，保存 `relative_path -> content_hash -> file_id`。
6. 对关键问题执行检索验证。
7. 输出成功、失败、跳过数量和失败明细。

### 5.2 增量新增

同步程序扫描到同步状态中不存在的来源路径时，将其作为新增文件处理。任务成功后才写入本地同步状态。

### 5.3 更新已有内容

同一路径的本地哈希发生变化时：

1. 上传和入库新文件。
2. 等待任务成功。
3. 执行必要的检索验证。
4. 删除状态中保存的旧 `file_id`。
5. 原子更新本地状态文件。

平台上传接口会对目标知识库做内容哈希检查，相同内容返回 HTTP 409。调用方应将它视为“内容已经存在”，不要无限重试。

### 5.4 删除来源数据

来源系统删除并不必然意味着应该删除知识。建议默认只报告差异；只有业务明确要求镜像同步时，才启用删除：

1. 计算上次状态中存在、当前来源中不存在的路径。
2. 记录待删除清单并审计。
3. 调用 `DELETE .../documents/{doc_id}`。
4. 成功后从同步状态移除。

Python 示例必须显式传 `--delete-removed` 才会执行该操作。

## 6. 任务轮询与重试

推荐轮询间隔从 2 秒开始，长任务逐步增加到 10 秒；客户端必须设置总超时。不要在 `pending/running` 时重复提交同一个文件。

| HTTP/业务状态 | 含义 | 建议动作 |
| --- | --- | --- |
| `400` | 参数、文件类型、文件大小或处理配置错误 | 不自动重试，修正输入 |
| `401` | Key 无效、禁用或过期 | 停止任务并告警 |
| `403` | 缺少功能权限或资源管理权 | 停止任务，联系管理员 |
| `404` | 知识库、文档或任务不存在 | 核对环境和 ID；任务可能已被清理 |
| `409` | 相同内容已存在或知识库名称冲突 | 作为幂等冲突处理，不盲目重试 |
| `422` | 请求 JSON 与接口模型不匹配 | 不自动重试，修正代码 |
| `429` | 请求过多 | 按 `Retry-After` 或指数退避 |
| `5xx` | 服务或依赖暂时失败 | 指数退避并限制次数，仍失败则告警 |
| task `failed` | 解析、OCR、Embedding 或入库失败 | 保存 `task.error/result`，按根因处理 |

推荐退避：`2s -> 5s -> 10s -> 30s`，最多 4 次；文件处理任务是否能够安全重提，需要结合文档列表和内容哈希检查判断。

## 7. 检索验证

任务 `success` 证明处理链路结束，但生产同步还应验证关键知识是否能被检索：

```bash
curl --fail-with-body -X POST \
  -H 'Authorization: Bearer yxkey_xxxxxxxxx' \
  -H 'Content-Type: application/json' \
  -d '{"query":"设备 E-102 的额定压力是多少？","meta":{}}' \
  'https://superchuchu.example.com/api/knowledge/databases/kb_example/query'
```

验收时不要只断言 HTTP 200，应至少验证：

- 响应业务状态成功。
- 返回结果不为空。
- 关键答案或来源文件符合预期。
- 更新后检索结果来自新版本，不再依赖旧版本。

## 8. Python 示例工程

示例位于 `examples/knowledge-sync/python`，只使用 Python 标准库，无第三方依赖。

```bash
cd examples/knowledge-sync/python
cp .env.example .env.local

export YUXI_BASE_URL='https://superchuchu.example.com'
export YUXI_API_KEY='yxkey_xxx'
export YUXI_KB_ID='kb_xxx'

python3 sync_knowledge.py ./source-documents \
  --state-file .yuxi-sync-state.json \
  --verify-query '设备 E-102 的额定压力是多少？'
```

镜像删除来源中已经消失的文件：

```bash
python3 sync_knowledge.py ./source-documents \
  --state-file .yuxi-sync-state.json \
  --delete-removed
```

首次运行时自动创建知识库：

```bash
unset YUXI_KB_ID
python3 sync_knowledge.py ./source-documents \
  --create-kb-name 'MES 设备知识库' \
  --embedding-model-spec 'your-provider/your-embedding-model'
```

同步状态包含第三方来源路径、内容哈希、超级楚楚 `file_id` 和更新时间。旧版本删除失败时还会保存 `stale_file_ids`，下次运行自动重试清理。状态文件属于生产数据，应持久化到可靠存储并备份；多个同步实例不能同时写同一个状态文件。

## 9. 联调验收清单

### 认证与权限

- [ ] 生产地址使用 HTTPS。
- [ ] 使用独立集成账号和独立 API Key。
- [ ] Key 不出现在源码、日志、Postman 导出值或错误截图中。
- [ ] Key 只拥有目标知识库同步所需权限。
- [ ] 已验证禁用或过期 Key 返回 401。

### 主流程

- [ ] 能列出或创建目标知识库。
- [ ] 能上传至少一种真实生产文件。
- [ ] 能取得 `file_path/content_hash/size`。
- [ ] 能提交 `auto_index=true` 任务并等待 `success`。
- [ ] 能保存返回的 `file_id`。
- [ ] 能通过知识检索查到新内容。

### 数据生命周期

- [ ] 重复运行不会重复创建未变化文档。
- [ ] 内容变化时先入库新版本，再删除旧版本。
- [ ] 新版本失败时旧知识仍然可用。
- [ ] 删除来源数据需要显式配置并留下审计记录。
- [ ] 同步状态有备份和恢复方案。

### 稳定性

- [ ] 所有 HTTP 请求都有连接和读取超时。
- [ ] 401/403/400/422 不会无限重试。
- [ ] 429/5xx 使用有限指数退避。
- [ ] 后台任务有总超时、失败告警和人工补偿入口。
- [ ] 日志包含来源系统、来源 ID、`kb_id/file_id/task_id`，但不包含完整 API Key。

## 10. 当前边界

当前接口具备 API Key、上传、解析、向量入库、任务查询、删除和检索能力，能够完成内部系统的自动知识同步。但接入方必须理解以下限制：

1. 没有 Key 自身的权限 scope；Key 继承关联用户的全部权限。
2. 没有 `external_id` 原生 upsert；接入方要维护业务主键与 `file_id` 的映射。
3. 上传与入库是两个请求，不是事务；上传后第二步失败可能留下暂存对象。
4. 没有 Webhook；任务只能轮询。
5. 没有通用 `Idempotency-Key`；当前主要依靠内容哈希冲突和接入方状态防重。
6. 知识处理 coroutine 运行在 API 进程内，任务元数据虽会持久化，但进程重启不保证自动恢复原执行现场。
7. 当前路由没有独立 `/api/v1/integrations` 版本承诺，升级前应执行回归联调。

如果需要面向多个外部客户提供高可靠公共服务，后续应增加版本化集成 API、`external_id/source_version` upsert、Key 级知识库范围、Webhook、原子提交或补偿清理，以及可恢复的独立任务执行器。

## 11. 排障信息

向超级楚楚维护方提交问题时，请提供：

```text
发生时间和时区：
调用环境/base_url：
来源系统与来源记录 ID：
HTTP 方法和路径（不要附完整 API Key）：
HTTP 状态码和响应 detail：
kb_id：
task_id：
file_id：
文件扩展名和大小：
是否启用 OCR：
重试次数：
```

不要发送完整 API Key、生产文件正文或包含敏感数据的请求日志。
