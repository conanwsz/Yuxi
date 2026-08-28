# 第三方 OIDC 单点登录

Yuxi 可以作为独立的 OpenID Connect（OIDC）Client 接入公司统一认证。协议使用 OAuth 2.0 Authorization Code + PKCE（S256）：认证中心负责认证，Yuxi 负责建立和校验自己的本地 JWT 登录态。

每个项目、每个环境都必须向认证中心申请独立 Client。不要复用其他项目的 `client_id`、`client_secret`、回调地址或 App Scheme；Cornex Meet 的登录、回调、完成登录和退出接口也是其内部实现，不是可供 Yuxi 调用的通用 SSO API。

## 接入前准备

向统一认证平台管理员提供以下注册信息：

- 项目名称和环境；测试、预发布、生产必须分别登记。
- Client 类型：Yuxi 服务端部署使用 **Confidential Client**。
- `grant_type=authorization_code`、`response_type=code`。
- Scope：`openid profile email`。
- PKCE：必须启用 `S256`。
- 完整的 HTTPS Redirect URI，以及（如认证中心支持统一退出）完整的 Logout Redirect URI。

Redirect URI 不支持通配符，且必须与平台注册值逐字符一致。Yuxi 默认回调地址为：

```text
https://<your-yuxi-host>/api/auth/oidc/callback
```

认证中心参考地址如下；正式接入始终以平台管理员下发的 Issuer、Client 和回调登记结果为准：

| 环境 | Issuer Base URL |
| --- | --- |
| 测试/开发 | `https://cnoidc.t.cn-np.com` |
| 生产 | `https://cnoidc.cn-np.com` |

## 配置 Yuxi

在部署使用的 `.env` 中配置：

```dotenv
OIDC_ENABLED=true
OIDC_PROVIDER_NAME=统一认证
OIDC_PROVIDER_TYPE=standard

# 首选变量：Issuer 根地址，Yuxi 从其 Discovery 文档读取协议端点。
OIDC_ISSUER_BASE_URL=https://cnoidc.t.cn-np.com

OIDC_CLIENT_ID=<由认证中心为本环境单独下发>
OIDC_CLIENT_SECRET=<仅服务端保存的密钥>
OIDC_ID_TOKEN_ALGORITHMS=RS256
OIDC_REDIRECT_URI=https://<your-yuxi-host>/api/auth/oidc/callback
OIDC_SCOPES=openid profile email
```

服务端优先读取 `OIDC_ISSUER_BASE_URL`，仅在该变量未设置时回退读取旧配置名 `OIDC_ISSUER_URL`；不要同时配置两个变量。不要在前端构建变量、前端资源、代码仓库、请求 URL 或普通日志中存放或输出 `OIDC_CLIENT_SECRET`、授权码、Token、`state`、`nonce` 或 PKCE verifier。

### Provider 适配器

`OIDC_PROVIDER_TYPE` 选择后端内置的 OIDC Provider 适配器。当前内置类型如下：

| 类型 | 适用范围 | 回调方式 |
| --- | --- | --- |
| `standard` | 符合标准 Discovery、Token 和 UserInfo 结构的 OIDC Server | GET Query 或 `response_mode=form_post` |
| `cnnp` | CNNP 标准协议端点，以及公司、部门编码扩展字段 | GET Query 或 `response_mode=form_post` |

未配置时默认使用 `standard`。配置值不在内置注册表中时，服务会明确拒绝启动 OIDC 流程，不会静默回退。Provider 显示名称仍由 `OIDC_PROVIDER_NAME` 控制，与适配器类型无关。

适配器只处理授权参数、回调承载、Token/UserInfo 请求及 Claim 结构差异。`state`、`nonce`、PKCE、ID Token 验签、Issuer 校验以及 `issuer + sub` 身份绑定始终由 Yuxi 核心流程执行，不能由适配器关闭。新增 Provider 类型前必须提供脱敏的 callback、Token、UserInfo/ID Token 样例及契约测试；不要通过放宽安全校验兼容不合规 Server。

### CNNP 组织字段同步

CNNP 认证中心使用标准 OIDC 协议，并在 UserInfo 中增加以下字段。启用时配置 `OIDC_PROVIDER_TYPE=cnnp`：

| UserInfo 字段 | 超级楚楚字段 | 同步语义 |
| --- | --- | --- |
| `entity_code` | 公司编码 | 唯一匹配或创建公司主体根节点。 |
| `entity_short_name` | 主体 OIDC 名称 | 认证中心更新时同步，不覆盖管理员本地名称。 |
| `dept_code` | 部门编码 | `-BM` 后每两位一级，唯一确定部门身份和父子关系。 |
| `dept_name` | 部门 OIDC 名称 | 只补充或更新当前编码节点的 OIDC 名称。 |

例如 `JXI-BM460503` 会解析为 `JXI-BM46 / JXI-BM4605 / JXI-BM460503`。缺少名称的父级先以编码展示，父级人员后续登录时补齐 OIDC 名称。管理员可设置独立的本地名称作为展示别名，但不能修改编码或移动 OIDC 节点。

CNNP 用户每次登录都会同步主部门；切换公司主体时，仅保留新主体内的兼职部门。`entity_code` 或 `dept_code` 缺失、格式不合法时，登录继续完成，但用户主部门会同步到 `OIDC_DEFAULT_DEPARTMENT`。编码和名称来自 UserInfo 响应，不是浏览器回调 query 参数。

修改 `.env` 后，Compose 已创建的容器不会自动读取新环境变量。重建 API 容器使配置生效：

```bash
docker compose up -d --no-deps --force-recreate api
```

## 登录协议与安全约束

Yuxi 通过下列 Discovery 地址获取 `issuer`、`authorization_endpoint`、`token_endpoint`、`userinfo_endpoint`，以及认证中心提供时的 `end_session_endpoint`；不要把这些端点路径硬编码或配置为其他项目的地址：

```text
GET {OIDC_ISSUER_BASE_URL}/.well-known/openid-configuration
```

每次登录必须在服务端创建短期、一次性的登录事务，至少保存：

- `state`：绑定登录事务并防止 CSRF；回调时必须先校验、过期拒绝并一次性消费。
- `nonce`：绑定登录事务，用于校验 ID Token，防止重放。
- `code_verifier`：仅保存在服务端；`code_challenge` 为 `BASE64URL(SHA256(code_verifier))`。
- 初始站内跳转路径和创建时间；跳转目标必须限制为 Yuxi 站内安全路径。

授权请求必须携带：

```text
response_type=code
client_id={Yuxi Client ID}
redirect_uri={已登记的 Yuxi Redirect URI}
scope=openid profile email
state={一次性随机值}
nonce={一次性随机值}
code_challenge={BASE64URL(SHA256(code_verifier))}
code_challenge_method=S256
```

认证中心回调后，Yuxi 必须先校验 `state`，再由服务端以授权码、完全相同的 `redirect_uri`、本项目 Client 凭据和原始 `code_verifier` 向 Discovery 的 `token_endpoint` 换取 Token。不得在浏览器中交换授权码，也不得重复使用授权码。

除请求 UserInfo 所需的 `access_token` 外，服务端还必须通过认证中心的签名密钥校验 ID Token 的签名、`iss`、`aud`、`exp`、`iat` 和 `nonce`；只解码 JWT Payload 不能作为身份校验。

`OIDC_ID_TOKEN_ALGORITHMS` 是 Yuxi Client 自己的算法白名单，默认仅允许 `RS256`。只有同时出现在该白名单和 Discovery 能力中的算法才会被接受；不要为了兼容失败而配置 `none` 或无关的对称/非对称算法。

## 用户身份映射

通过 Discovery 的 `userinfo_endpoint` 使用 `Authorization: Bearer {access_token}` 获取用户信息。所需或建议字段如下：

| 字段 | 用途 |
| --- | --- |
| `sub` | 必须存在；稳定外部身份主体。 |
| `email` | 用于首次用户关联或创建时的联系信息；不得替代主身份键。 |
| `name` | 可选显示名称。 |
| `preferred_username` | `name` 缺失时的显示名称兜底。 |
| `picture` | 可选头像。 |
| `entity_code` | `cnnp` 适配器使用的公司编码。 |
| `entity_short_name` | `cnnp` 适配器使用的公司简称。 |
| `dept_code` | `cnnp` 适配器使用的层级部门编码。 |
| `dept_name` | `cnnp` 适配器使用的部门名称。 |

Yuxi 必须以 `issuer + sub`（例如 `issuer|sub`）作为外部身份的唯一键；不能使用 email、用户名或显示名称作为跨认证中心的稳定身份。若启用既有账号的 email 自动关联，必须规范化 email、拒绝已关联到其他 OIDC 身份的冲突，并在缺少首次映射所需字段时拒绝自动登录，而不是猜测用户身份。

升级旧版 Yuxi 时，历史 `oidc:{sub}` 记录没有保存 issuer，系统不会自动把它绑定给当前认证中心。只有确认原认证中心 Issuer 后，才可临时设置 `OIDC_LEGACY_ISSUER_URL=<原 Issuer>` 完成受控迁移；更换 IdP 时不得使用该配置认领旧记录，迁移完成后应移除该变量。

认证中心的 Token 不是 Yuxi 的业务会话。身份校验成功后，Yuxi 仅签发自己的本地 JWT 登录态；不得将 access token 或 ID token 放入 URL。

## 失败与退出行为

以下情况必须拒绝登录并提供不泄漏敏感信息的错误提示：回调缺少/未知/过期/已消费的 `state`、认证中心返回 `error`、授权码重复或过期、PKCE verifier 或 Redirect URI 不匹配、Discovery 缺少必需端点、ID Token 校验失败、UserInfo 缺少 `sub`，以及本地身份映射冲突。

当前 Yuxi 退出仅清理本地 JWT 登录态，尚未接通认证中心的 `end_session_endpoint` 或统一退出回调。因此，退出 Yuxi 后认证中心浏览器会话可能仍存在，后续登录可能无需再次输入凭据；界面和运维说明不得将此称为“退出统一认证”。

## 验收清单

- [ ] 每个环境均登记独立 Confidential Client、精确的 HTTPS 回调地址和需要时的退出回调地址。
- [ ] 服务端通过 OIDC Discovery 获取协议端点，授权流为 Authorization Code + PKCE S256。
- [ ] `OIDC_PROVIDER_TYPE` 是已登记的内置适配器；非标准适配器有脱敏 fixture 和契约测试。
- [ ] `state`、`nonce`、`code_verifier` 只保存在短期服务端事务中，`state` 仅能消费一次。
- [ ] 回调在换取 Token 前完成 state 校验；ID Token 完成签名、issuer、audience、时间和 nonce 校验。
- [ ] UserInfo 的 `sub` 与 Issuer 组成外部身份键；email 不作为主身份。
- [ ] Client Secret、授权码、Token、PKCE verifier 未进入前端、仓库、URL 或普通日志。
- [ ] 登录失败、取消、过期/重复回调、Token 校验失败和身份冲突均有明确拒绝行为。
- [ ] 已验证当前退出只清理 Yuxi 本地 JWT，而非统一认证会话。
