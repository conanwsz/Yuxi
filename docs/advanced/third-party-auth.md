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

# 首选变量：Issuer 根地址，Yuxi 从其 Discovery 文档读取协议端点。
OIDC_ISSUER_BASE_URL=https://cnoidc.t.cn-np.com

OIDC_CLIENT_ID=<由认证中心为本环境单独下发>
OIDC_CLIENT_SECRET=<仅服务端保存的密钥>
OIDC_ID_TOKEN_ALGORITHMS=RS256
OIDC_REDIRECT_URI=https://<your-yuxi-host>/api/auth/oidc/callback
OIDC_SCOPES=openid profile email
```

服务端优先读取 `OIDC_ISSUER_BASE_URL`，仅在该变量未设置时回退读取旧配置名 `OIDC_ISSUER_URL`；不要同时配置两个变量。不要在前端构建变量、前端资源、代码仓库、请求 URL 或普通日志中存放或输出 `OIDC_CLIENT_SECRET`、授权码、Token、`state`、`nonce` 或 PKCE verifier。

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

Yuxi 必须以 `issuer + sub`（例如 `issuer|sub`）作为外部身份的唯一键；不能使用 email、用户名或显示名称作为跨认证中心的稳定身份。若启用既有账号的 email 自动关联，必须规范化 email、拒绝已关联到其他 OIDC 身份的冲突，并在缺少首次映射所需字段时拒绝自动登录，而不是猜测用户身份。

升级旧版 Yuxi 时，历史 `oidc:{sub}` 记录没有保存 issuer，系统不会自动把它绑定给当前认证中心。只有确认原认证中心 Issuer 后，才可临时设置 `OIDC_LEGACY_ISSUER_URL=<原 Issuer>` 完成受控迁移；更换 IdP 时不得使用该配置认领旧记录，迁移完成后应移除该变量。

认证中心的 Token 不是 Yuxi 的业务会话。身份校验成功后，Yuxi 仅签发自己的本地 JWT 登录态；不得将 access token 或 ID token 放入 URL。

## 失败与退出行为

以下情况必须拒绝登录并提供不泄漏敏感信息的错误提示：回调缺少/未知/过期/已消费的 `state`、认证中心返回 `error`、授权码重复或过期、PKCE verifier 或 Redirect URI 不匹配、Discovery 缺少必需端点、ID Token 校验失败、UserInfo 缺少 `sub`，以及本地身份映射冲突。

当前 Yuxi 退出仅清理本地 JWT 登录态，尚未接通认证中心的 `end_session_endpoint` 或统一退出回调。因此，退出 Yuxi 后认证中心浏览器会话可能仍存在，后续登录可能无需再次输入凭据；界面和运维说明不得将此称为“退出统一认证”。

## 验收清单

- [ ] 每个环境均登记独立 Confidential Client、精确的 HTTPS 回调地址和需要时的退出回调地址。
- [ ] 服务端通过 OIDC Discovery 获取协议端点，授权流为 Authorization Code + PKCE S256。
- [ ] `state`、`nonce`、`code_verifier` 只保存在短期服务端事务中，`state` 仅能消费一次。
- [ ] 回调在换取 Token 前完成 state 校验；ID Token 完成签名、issuer、audience、时间和 nonce 校验。
- [ ] UserInfo 的 `sub` 与 Issuer 组成外部身份键；email 不作为主身份。
- [ ] Client Secret、授权码、Token、PKCE verifier 未进入前端、仓库、URL 或普通日志。
- [ ] 登录失败、取消、过期/重复回调、Token 校验失败和身份冲突均有明确拒绝行为。
- [ ] 已验证当前退出只清理 Yuxi 本地 JWT，而非统一认证会话。
