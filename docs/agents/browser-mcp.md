# 浏览器工具 MCP

测试同事可以使用 Yuxi 智能体做"模拟人工操作浏览器"的自动化测试。智能体获得一组浏览器自动化工具（导航、点击、填表、截图、执行 JS），并能在 chat 状态面板实时看到浏览器正在做什么。

## 启用方法

1. 进入「扩展」→「MCP 管理」
2. 找到 `mcp-playwright`（内置默认注册，未启用时显示"禁用"）
3. 点击启用
4. 在 agent 配置的"MCP 工具"中选择 `mcp-playwright`

## agent 可用工具

启用后，agent 工具列表会增加以下官方 mcp-playwright 工具：

- `browser_navigate`：导航到 URL
- `browser_click`：点击元素（通过 accessibility ref）
- `browser_fill`：填表
- `browser_screenshot`：截图（返回 base64）
- `browser_snapshot`：获取页面 accessibility 树
- `browser_evaluate`：执行 JS
- 其他官方工具（参考 mcp-playwright 文档）

## 实时视图

启用后，chat 会话在第一次使用浏览器工具时会自动展开状态面板，实时以 16:9 比例显示浏览器截图（每 1.5 秒刷新一次）。状态面板可手动关闭（关闭后当前会话不会强行再开），但**不会中断 agent 运行**。超级管理员还可展开查看 DOM 页面快照。

## 多用户隔离

每个用户的 cookie / 登录态完全独立。A 用户登录某个被测网站后，B 用户看不到。

## 资源与限制

| 配置项 | 默认值 | 说明 |
|---|---|---|
| 最大并发用户数 | 30 | 超出时新请求返回 503 |
| 空闲回收时间 | 30 分钟 | 无活动后自动关闭 context |
| SSE 断开宽限期 | 5 分钟 | 客户端断流后给用户重连的缓冲 |
| 浏览器实例内存上限 | 8 GB | mcp-playwright 容器上限 |
| 单 context 估算内存 | 100-500 MB | 普通页面约 200 MB，现代 SPA 可达 500 MB |

## 架构

```
agent (worker-dev)
   │ ① MCP 工具调用
   ▼
mcp-playwright 容器（官方 Playwright 镜像）
   │
   ▼
Chromium（容器内 1 个进程，按 user_id 多 context）

前端 BrowserStateSection（web/src/components/BrowserStateSection.vue）
   │ ② SSE 订阅
   ▼
browser-viewer 容器（自建 FastAPI）
   │ 周期性拉取截图
   ▼
mcp-playwright
```

## 故障排查

| 现象 | 可能原因 | 处理 |
|---|---|---|
| 状态面板一直"浏览器不可用" | browser-viewer 容器未起 | `docker compose ps browser-viewer`，必要时 `docker compose up -d browser-viewer` |
| 状态面板一直"连接中" | browser-viewer 无法连 mcp-playwright | `docker compose logs browser-viewer`，检查 `MCP_PLAYWRIGHT_URL` 环境变量 |
| agent 调工具返回 502 | 同上 | 同上 |
| 状态面板显示"重连中"超过 1 分钟 | 网络抖动 / viewer 容器过载 | `docker compose restart browser-viewer` |
| 并发 503 | 同时活跃用户超过 30 | 等待空闲回收，或联系管理员调整 `BROWSER_VIEWER_MAX_CONTEXTS` |

## 已知边界（YAGNI）

当前不在支持范围内（已列入后续改进）：

- cookie / storage 持久化（重启 browser-viewer 后用户需要重新登录被测网站）
- Playwright trace viewer 集成
- 录视频 / 截图历史回放
- 多 page 同步、跨 context 编排
- 反爬 / proxy / 指纹伪装
- viewer 集群化（多实例 + sticky session）