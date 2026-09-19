# 浏览器工具 MCP 集成设计

> 日期：2026-09-19
> 状态：已批准，待实现
> 范围：给 Yuxi 智能体增加浏览器自动化能力，让 agent 模拟人工操作浏览器去做测试

## 1. 背景与目标

测试同事需要使用 Yuxi 智能体做"模拟人工操作浏览器"的自动化测试。Yuxi 当前架构不包含浏览器能力，需要新增。

需求要点：
- agent 可以调用浏览器工具（导航、点击、填表、截图、执行 JS、获取页面快照）
- 用户在 chat 前台能通过小窗实时看到浏览器正在做什么（不需要特别清晰）
- 多用户隔离（cookie / 登录态不串）
- 权限沿用 Yuxi 现有 MCP governance
- 不引入新的权限层

不在本次范围（YAGNI）：
- cookie / storage 持久化（重启会丢登录态，作为 Y2 改进项）
- 录视频 / 截图历史回放
- 多 page 同步编排 / 跨 context 协作
- 反爬 / proxy / 指纹伪装

## 2. 架构

新增两个独立服务，均接入 Docker Compose。

```
agent (worker-dev)
    │ ① 浏览器工具调用（Yuxi MCP 客户端）
    ▼
mcp-playwright 容器（官方镜像 mcp/playwright:latest）
    │ Playwright 协议
    ▼
Chromium 进程（容器内 1 个，多 context）

前端 BrowserDrawer（web/src/components/agent/BrowserDrawer.vue）
    │ ② SSE 订阅 user_id 的浏览器视图流
    ▼
browser-viewer 容器（自建 FastAPI + SSE）
    │ 周期性调 mcp-playwright 的 browser_take_screenshot / browser_snapshot
    ▼
mcp-playwright（HTTP/SSE MCP，复用连接）
```

### 2.1 组件职责

| 组件 | 职责 |
|---|---|
| mcp-playwright | Playwright 协议服务端；通过 Yuxi 现有 MCP 注册中心被发现；为每个 user_id 维护独立的 browser context |
| browser-viewer | ① 维护 `user_id → context_id` 映射；② 暴露 SSE 端点 `/browser/stream/{user_id}`；③ 后台轮询线程周期性截图 + snapshot 推送；④ 空闲回收定时任务；⑤ 并发计数 + watchdog |
| BrowserDrawer.vue | 右侧抽屉；SSE 客户端；展示截图 + 当前 URL + 上次操作摘要 |

## 3. 数据流

### 3.1 Agent 调用浏览器工具

1. 用户在 chat 输入 → AgentChatComponent 发请求
2. agent 推理 → 决定调 `browser_navigate(url)`
3. worker 通过 Yuxi MCP 客户端向 mcp-playwright 发 tool call（HTTP/SSE MCP）
4. mcp-playwright 在 user_id 对应的 context 上调 `page.goto(url)`
5. 返回工具结果 `{ content: [...], isError: false }`
6. agent 继续推理

### 3.2 前端看到浏览器画面

1. 用户进入 `/agent` 路由 → BrowserDrawer 组件挂载（不自动展开）
2. SSE 连接就绪 + 用户未禁用 → 抽屉可见
3. 首次订阅时调 `POST /browser/context/ensure {user_id}` → viewer 触发 mcp-playwright 创建或复用 context，返回 `context_id`
4. 浏览器订阅 EventSource(`/browser/stream/{user_id}`)
5. viewer 后台线程循环（默认 1.5s 间隔）：
   - 调 mcp-playwright `browser_take_screenshot` 拿 base64
   - 调 mcp-playwright `browser_snapshot` 拿 accessibility 摘要
   - 推 `{ts, screenshot_b64, url, title, snapshot, action_summary, status}` 给前端
6. 前端按最新截图渲染抽屉（base64 内联到 `<img>`）

### 3.3 空闲回收

viewer 维护 `last_activity_ts[user_id]`：
- 任何 MCP 调用（来自 agent）触发该用户 → 更新 timestamp
- SSE 连接断开（用户退出页面）→ 不立即回收，等 5 分钟缓冲（避免误杀）
- 后台定时任务每分钟扫描：`last_activity_ts > 30 分钟` → 调 mcp-playwright `browser_close_context`
- viewer 进程重启 → 所有 context 标记 dirty，用户回来重新 ensure（cookie 会丢，符合 YAGNI 边界）

## 4. 关键状态

| 状态 | 存储位置 | 生命周期 |
|---|---|---|
| `user_id → context_id` 映射 | viewer 内存 dict | viewer 进程生命周期 |
| `last_activity_ts[user_id]` | viewer 内存 | viewer 进程生命周期 |
| context 内的 cookie / storage | chromium 进程内存 | context 生命周期（受 idle 回收影响） |
| SSE 订阅者列表 | viewer 内存 | SSE 连接寿命 |
| dirty 标记（viewer 重启后需重建） | viewer 内存 | viewer 进程生命周期 |

无持久化。重启 mcp-playwright 或 viewer 容器后，cookie / 登录态丢失（用户在 UI 上被告知）。

## 5. 前端集成

### 5.1 BrowserDrawer 组件

- 默认宽度 480px，可手动调整 320-720px
- 内容三块：截图（主体） + 当前 URL + 上次操作摘要（snapshot 文字）
- SSE 断开自动重连（指数退避，初始 1s，封顶 30s）
- 抽屉关闭时**不触发 context 回收**（仅关 SSE，不调 `browser_close_context`）
- 服务端事件 schema：
  ```ts
  type StreamEvent = {
    ts: number
    screenshot_b64: string
    url: string
    title: string
    snapshot: string        // accessibility 摘要
    action_summary: string  // 上一次调用的工具名 + 关键参数
    status: 'active' | 'closed' | 'recovering' | 'unavailable'
  }
  ```

### 5.2 集成点

- `AgentView.vue` 引入 `<BrowserDrawer :user-id="currentUser.id" />`
- SSE 鉴权：viewer 接 Yuxi 现有 session cookie，不引入新机制
- 抽屉默认隐藏状态保存在 Pinia，可被"禁用"永久关闭

## 6. 部署

新增到 `docker-compose.yml`：

```yaml
mcp-playwright:
  image: mcp/playwright:latest
  container_name: mcp-playwright
  ports:
    - "8931:8931"   # MCP HTTP/SSE 端点
  environment:
    - PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
  volumes:
    - /ms-playwright
  deploy:
    resources:
      limits:
        memory: 8G
  shm_size: '2gb'   # Chromium 必需

browser-viewer:    # 独立容器（方案 X）
  build: ./backend
  container_name: browser-viewer
  command: uv run python -m yuxi.agents.browser_viewer.server
  ports:
    - "8932:8932"   # viewer HTTP/SSE
  environment:
    - MCP_PLAYWRIGHT_URL=http://mcp-playwright:8931
    - BROWSER_VIEWER_MAX_CONTEXTS=30
    - BROWSER_VIEWER_IDLE_TIMEOUT=1800   # 30 分钟
  depends_on:
    - mcp-playwright
  deploy:
    resources:
      limits:
        memory: 1G
```

viewer 与 mcp-playwright 通信走 **HTTP/SSE MCP 协议**，避免 stdio 容器耦合。

### 6.1 默认配置

| 项 | 默认值 | 备注 |
|---|---|---|
| MAX_CONCURRENT_CONTEXTS | 30 | 并发上限，超出排队 |
| IDLE_TIMEOUT_SECONDS | 1800（30 分钟） | 空闲回收 |
| SSE_POLL_INTERVAL_MS | 1500 | viewer 截图拉取间隔 |
| SSE_DISCONNECT_GRACE_SECONDS | 300（5 分钟） | SSE 断开后宽限期 |

### 6.2 资源预算

- 每个空 context：~30-80 MB
- 加载普通页面：~100-200 MB
- 现代 SPA：~300-500 MB
- 单 context 多 tab：+100-300 MB / tab

100 用户 × 平均 300 MB ≈ 30 GB。空闲回收后实际线上同时活跃用户约 5-15 个，资源压力可控。

## 7. 错误处理

| 错误场景 | 处理 |
|---|---|
| mcp-playwright 容器挂 | viewer 缓存下次重试；前端抽屉 status=unavailable |
| Chromium 进程崩 | viewer 收到错误，把该 user_id 标记 dirty；下次 ensure 自动重建 |
| 单 context OOM | viewer watchdog 强制 close；推 `{status: 'closed', reason: 'oom'}` |
| viewer 自身重启 | 所有 context 标记 dirty；用户重连时按需重建 |
| 并发超 30 | 新请求 503；前端抽屉提示"请稍后再试" |
| SSE 断流 | 前端指数退避重连；重连失败超过 5 次则提示用户检查网络 |

## 8. 测试

按 Yuxi 测试分层（`backend/test/{unit,integration,e2e}` 和 `web/test/unit`）。

### 8.1 后端

- `backend/test/unit/agents/test_browser_viewer_state.py`
  - `test_context_mapping_lifecycle`：ensure / close / dirty 标记
  - `test_idle_reclaim_evicts_after_timeout`：模拟时间推进
  - `test_concurrent_limit_rejects_overflow`：mock 31 个并发请求
  - `test_oom_watchdog_force_closes`

- `backend/test/integration/api/test_browser_stream.py`
  - 真实起 mcp-playwright 容器，创建测试 context
  - `test_sse_emits_screenshot_within_poll_interval`
  - `test_sse_event_schema_matches_spec`

- `backend/test/integration/api/test_browser_mcp_routing.py`
  - 验证 Yuxi agent 通过 MCP 注册中心能发现并调用 `browser_*` 工具

### 8.2 前端

- `web/test/unit/components/BrowserDrawer.test.js`
  - `test_subscribes_to_sse_on_mount`
  - `test_renders_latest_screenshot`
  - `test_reconnects_with_exponential_backoff_on_disconnect`
  - `test_close_drawer_does_not_trigger_context_reclaim`

### 8.3 端到端

- `backend/test/e2e/test_browser_agent_flow.py`
  - 配置一个最小 agent，启用 mcp-playwright MCP
  - 通过 chat 触发 navigate → 验证截图出现在 MinIO / 推流给 SSE 客户端
  - 验证 idle 回收

## 9. 验收标准

1. ✅ Yuxi 后端启动后，mcp-playwright 与 browser-viewer 两个新容器 healthy
2. ✅ 登录用户 A 在 `/agent` 启用 MCP `mcp-playwright` 后，agent 工具列表出现 `browser_navigate` / `browser_click` 等官方 mcp-playwright 工具
3. ✅ agent 调 `browser_navigate` 后，用户 A 的 BrowserDrawer 抽屉自动展开，显示当前页面截图
4. ✅ 用户 B 同时启用同一 MCP，agent 调 `browser_navigate` 后，用户 A 与 B 的抽屉显示各自独立页面（cookie / 登录态不串）
5. ✅ 用户 A 关闭抽屉 30 分钟后，viewer 后台关闭其 context
6. ✅ 用户 A 重新打开抽屉，agent 再调 `browser_navigate` 时，viewer 自动重建 context
7. ✅ 同时启用 30 个用户后，第 31 个用户 ensure 返回 503
8. ✅ mcp-playwright 容器重启后，所有活跃用户下次 ensure 时自动重建 context（不报错）
9. ✅ 单 context 内存超阈值（如 1 GB），watchdog 强制 close 并推送 reason='oom' 给前端
10. ✅ 所有 §8 测试用例通过
11. ✅ 现有 e2e / integration 测试不回归

## 10. 文件落点（实现阶段待分配）

| 内容 | 路径 |
|---|---|
| browser-viewer 服务实现 | `backend/package/yuxi/agents/browser_viewer/server.py` |
| viewer 状态管理 | `backend/package/yuxi/agents/browser_viewer/state.py` |
| viewer SSE 端点 | `backend/package/yuxi/agents/browser_viewer/stream.py` |
| viewer 客户端（连 mcp-playwright） | `backend/package/yuxi/agents/browser_viewer/mcp_client.py` |
| MCP 注册：mcp-playwright | `backend/package/yuxi/agents/mcp/registry.py`（既有，加配置项） |
| 前端抽屉组件 | `web/src/components/agent/BrowserDrawer.vue` |
| 前端 SSE composable | `web/src/composables/useBrowserStream.js` |
| docker-compose 服务 | `docker-compose.yml`（追加 mcp-playwright + browser-viewer） |
| 文档（面向用户） | `docs/agents/browser-mcp.md`（Yuxi 文档站，需更新 `docs/.vitepress/config.mts`） |
| 单元测试 | `backend/test/unit/agents/test_browser_viewer_state.py` |
| 集成测试 | `backend/test/integration/api/test_browser_stream.py` |
| 前端单元测试 | `web/test/unit/components/BrowserDrawer.test.js` |
| 端到端测试 | `backend/test/e2e/test_browser_agent_flow.py` |
| 变更日志 | `docs/develop-guides/changelog.md` |

## 11. 后续改进项（不在本次）

- cookie / storage 持久化（重启保留登录态）
- Playwright trace viewer 集成（事后复盘）
- 多 page 同步、跨 context 编排
- 录视频 / 截图历史回放
- 反爬 / proxy / 指纹伪装
- viewer 集群化（多实例 + sticky session）