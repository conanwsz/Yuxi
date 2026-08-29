# 生产部署指南

本文档介绍如何在生产环境中部署 Yuxi。

## 前置要求

- Docker Engine (v24.0+)
- Docker Compose (v2.20+)
- NVIDIA Container Toolkit（如需使用 GPU 服务）

::: warning 注意事项
1. 生产环境和开发环境建议使用不同的机器，避免端口和资源冲突
2. 虽然名为「生产环境」，但这只是基本配置，真正上线需要根据实际情况调整
3. 前端有调试面板（长按侧边栏触发），生产环境建议关闭
:::

## 部署步骤

### 1. 准备配置文件

为避免与开发环境冲突，生产环境建议使用 `.env.prod` 文件：

```bash
cp .env.template .env.prod
```

编辑 `.env.prod`，设置强密码和必要的 API 密钥：

```sh
POSTGRES_PASSWORD=
NEO4J_PASSWORD=
MINIO_ACCESS_KEY=
MINIO_SECRET_KEY=
JWT_SECRET_KEY=
YUXI_INSTANCE_ID=
SANDBOX_PROVISIONER_TOKEN=
SILICONFLOW_API_KEY=
```

生产 Compose 会在前七项配置缺失或为空时拒绝启动，并提示具体变量名。`JWT_SECRET_KEY` 和 `SANDBOX_PROVISIONER_TOKEN` 均应至少使用 32 字节随机值并持久保存，可分别使用 `openssl rand -hex 32` 生成；两者不能复用。`YUXI_INSTANCE_ID` 应是每套部署稳定且唯一的实例标识。模型 API 密钥按实际使用的供应商配置。

### 2. 启动服务

使用生产环境配置文件启动：

```bash
# 仅启动核心服务（CPU 模式）
docker compose -f docker-compose.prod.yml up -d --build

# 启动所有服务（包含 GPU OCR）
docker compose -f docker-compose.prod.yml --profile all up -d --build
```

### 3. 验证部署

- Web 访问：http://localhost（直接通过 80 端口）
- API 健康检查：`curl http://localhost/api/system/health`

公开头像和 Agent 图片通过前端同源路径 `/minio/public/...` 读取，由 Nginx 只读代理到 MinIO 的 `public` bucket。无需也不应向公网开放 MinIO 的 `9000` 对象 API 或 `9001` 管理控制台；知识库等私有 bucket 不经过这个代理。需要使用独立静态资源域名时，可在 `.env.prod` 中设置 `MINIO_PUBLIC_URL=https://assets.example.com`，并在该域名侧保持同等的只读 bucket 限制。

历史 PDF 解析 Markdown 中已经写入的 `http://localhost:9000/public/...` 或其他 `<host>:9000/public/...` 图片地址，会在前端渲染时自动转换为同源路径，不需要重新解析 PDF。

## 跨域（CORS）配置

`docker-compose.prod.yml` 默认把 `YUXI_ENV` 设为 `production`，后端在该环境下会按 `YUXI_CORS_ORIGINS` 显式声明允许的来源。**未配置时返回空列表，浏览器跨域请求会被拒绝**。生产部署前请根据前端与 API 的相对位置选择策略：

| 部署形态 | 推荐配置 |
|----------|----------|
| 前端与 API 同源（Nginx 同端口反代） | 不需要设置，留空即可 |
| 前端与 API 跨域部署 | `YUXI_CORS_ORIGINS=https://your-frontend.example.com` |
| 多个前端域名 | 逗号分隔，如 `https://a.example.com,https://b.example.com` |
| 完全放开（不推荐） | `YUXI_CORS_ORIGINS=*`，会自动关闭 credentials，登录态/JWT 无法跨域携带 |

开发环境（`YUXI_ENV=development` 且未设置该变量）默认允许 `http://localhost:5173` 与 `http://127.0.0.1:5173`，方便本地前后端独立启动调试。从 0.7.0 升级到 0.7.1 时，如果此前是跨域部署但未显式声明来源，必须补上 `YUXI_CORS_ORIGINS`，否则前端跨域请求会被拒绝。

## 独立测试环境

测试环境与开发环境使用相同的后端、数据库、环境变量、持久化目录、容器名和宿主机端口。唯一差异是 `web`：开发环境运行 Vite 热更新服务器，测试环境执行 `vite build` 并由 Nginx 提供编译后的静态资源，从而避免 Vite 开发模式通过 JavaScript 注入 CSS。

测试配置要求 Docker Compose 2.24.4 或更高版本，以支持 `!override` 和 `!reset`。

::: danger 不要分两次启动
不能先执行 `docker compose up -d`，再执行 `docker compose -f docker-compose.test.yml up -d`。每条 Compose 命令都是一次独立配置解析，第二条命令不会继承第一条命令加载过的基础文件，而且测试覆盖文件本身没有完整的 API、数据库等服务定义。

必须在同一条命令中先传基础 `docker-compose.yml`，再传覆盖文件 `docker-compose.test.yml`。后续的 `up`、`ps`、`logs`、`down` 也必须使用相同的两个文件和相同顺序。
:::

推荐发布顺序如下。

### 1. 拉取代码并检查 Compose 版本

```bash
git pull
docker compose version
```

### 2. 预览最终合并配置

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.test.yml \
  config
```

合并结果中 `api`、数据库等服务应保持 dev 配置；`web` 应显示 `docker/web.test.Dockerfile`、容器内端口 `80`、宿主机端口 `5173`，且不再包含 `/app/src` 等源码挂载。

### 3. 一次性构建并启动

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.test.yml \
  up -d --build
```

如果服务器之前已经运行普通 dev，仍然只需要执行上面这一条组合命令。Compose 会保留配置没有变化的服务，只重新构建并重建 `web-dev`。

### 4. 查看容器和日志

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.test.yml \
  ps

docker compose \
  -f docker-compose.yml \
  -f docker-compose.test.yml \
  logs --tail=100 web
```

测试环境仍对外使用宿主机 `5173`，所以服务器外围 Nginx 的 `proxy_pass http://127.0.0.1:5173` 无需修改。容器内端口从 Vite 的 `5173` 变为 Nginx 的 `80`，由 Compose 保持宿主机端口不变。

编译会把项目全局样式变成 `/assets/*.css`，但 Ant Design Vue 的生产包仍会动态生成部分组件 `<style>`。如果外围 Nginx 只有 `default-src 'self'`，这些组件样式仍会被 CSP 拒绝。测试域名需要用 `docker/nginx/test-site.conf.example` 中的策略替换原 CSP，其中 `style-src 'self' 'unsafe-inline'` 专门允许组件动态样式；不要叠加两条 CSP，因为浏览器会同时执行两者，旧策略仍会产生拦截。

### 5. 验证测试环境已经使用编译产物

```bash
curl -s http://127.0.0.1:5173/ | grep -E '/@vite/client|/src/main.js'
# 正确结果：无输出。首页资源应为 /assets/*.js 和 /assets/*.css。

curl -s http://127.0.0.1:5173/ | grep '/assets/'
# 正确结果：能够看到 /assets/*.js 和 /assets/*.css。
```

### 6. 更新或停止

后续更新仍执行相同的组合命令：

```bash
git pull
docker compose \
  -f docker-compose.yml \
  -f docker-compose.test.yml \
  up -d --build
```

停止测试环境时也必须使用相同的两个文件：

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.test.yml \
  down
```

## 维护与更新

### 从使用默认凭据的版本升级

如果部署曾使用仓库历史默认的 PostgreSQL、Neo4j 或 MinIO 凭据，升级 Compose 文件本身不会保证已有数据卷中的服务凭据已经改变。升级前应分别通过对应服务的管理命令真实修改凭据，再把新值写入 `.env.prod`；完成后重建相关服务，并使用旧凭据验证登录已被拒绝。

PostgreSQL 可以在数据库容器内使用交互式命令修改，避免新密码出现在 shell 历史和进程参数中：

```bash
docker compose -f docker-compose.prod.yml exec postgres psql -U postgres -d yuxi -c '\password postgres'
```

Neo4j 应使用 `cypher-shell` 的当前用户密码修改流程；MinIO 应使用 `mc admin` 或部署所采用的密钥管理流程。不要把真实密码写入文档、测试脚本或命令历史。完成凭据轮换并配置 `SANDBOX_PROVISIONER_TOKEN` 后，再执行下面的重建命令。

### 更新代码

```bash
# 拉取最新代码
git pull

# 重新构建并启动
docker compose -f docker-compose.prod.yml up -d --build
```

生产 Compose 不再向宿主机发布 PostgreSQL 和文档解析服务端口。确需从宿主机维护时，优先使用 `docker compose exec`；不要为了临时调试把这些端口重新暴露到公网。

### 查看日志

```bash
# API 日志
docker logs -f api-prod

# Nginx 访问日志
docker logs -f web-prod
```
