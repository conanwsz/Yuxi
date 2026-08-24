# MiniMax Coding Plan MCP Docker 部署

该目录将 MiniMax 官方 `minimax-coding-plan-mcp` 封装为可迁移的 Docker 镜像，并通过 Streamable HTTP 暴露 MCP：

- MCP URL：`http://<部署主机>:18080/mcp`
- 健康检查：`http://<部署主机>:18080/healthz`
- 工具：`web_search`、`understand_image`

镜像固定 MiniMax MCP `0.0.4`、MCP Python SDK `1.29.0` 和 Supergateway `3.4.3`。官方包当前未限制 MCP SDK 的上限，直接安装可能解析到不兼容的 `2.x`；镜像已在构建阶段处理该兼容边界和 stdio 启动输出污染。

## 启动

```bash
cd docker/minimax-mcp
cp .env.example .env
```

编辑 `.env`，至少替换 `MINIMAX_API_KEY`，然后启动：

```bash
docker compose up -d --build
docker compose ps
curl http://127.0.0.1:18080/healthz
```

API Key 与 Host 必须属于同一区域：

- 中国大陆：`https://api.minimaxi.com`
- 海外：`https://api.minimax.io`

## 在 Yuxi 中添加

进入「智能体扩展 → MCP → 添加 MCP」，填写：

- MCP 标识：`minimax_web_search`
- MCP 名称：`MiniMax 网络搜索`
- 传输类型：`streamable_http`
- MCP URL：`http://<部署主机>:18080/mcp`
- HTTP 请求头：留空
- HTTP 超时：`60`
- SSE 读取超时：`300`

保存后点击「测试」，应发现 `web_search` 和 `understand_image` 两个工具。远程部署时，`understand_image` 应使用目标容器可访问的 HTTP/HTTPS 图片 URL；调用方机器上的本地文件路径对容器不可见。

## 迁移镜像

联网环境可直接复制本目录，在目标机器重新执行 `docker compose up -d --build`。离线迁移可以导出镜像：

```bash
docker save yuxi/minimax-coding-plan-mcp:0.0.4 | gzip > minimax-coding-plan-mcp-0.0.4.tar.gz
```

将压缩包、`compose.yaml` 和单独保管的 `.env` 复制到目标机器，然后执行：

```bash
gzip -dc minimax-coding-plan-mcp-0.0.4.tar.gz | docker load
docker compose up -d --no-build
```

## 网络安全

Compose 默认只把端口绑定到 `127.0.0.1`。如果 Yuxi 在另一台机器上，可把 `MINIMAX_MCP_BIND_ADDRESS` 改为内网 IP 或 `0.0.0.0`，但必须使用防火墙限制来源，或放在带 TLS 和鉴权的反向代理后。不要把该端口直接暴露到公网；任何能访问该 MCP 的客户端都能消耗对应 MiniMax 账户额度。
