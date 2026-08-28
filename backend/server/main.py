import asyncio
import os
import sys

# ==============================================================================
# 解决 Windows 下 psycopg 异步模式不支持 ProactorEventLoop 的问题
# 注意：这段代码必须放在应用的极早期，最好在导入 FastAPI 或初始化数据库之前
# ==============================================================================
if sys.platform == "win32":
    # 把当前文件 (main.py) 的上一级的上一级 (即根目录 Yuxi) 加入到 sys.path
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import time
from collections import defaultdict, deque

import uvicorn
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from yuxi import get_version

from server.routers import router
from server.utils.lifespan import lifespan
from server.utils.common_utils import setup_logging
from server.utils.access_log_middleware import AccessLogMiddleware

# 设置日志配置
setup_logging()

RATE_LIMIT_MAX_ATTEMPTS = 10
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_ENDPOINTS = {("/api/auth/token", "POST")}
DEFAULT_DEVELOPMENT_CORS_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")
EXPLICIT_CORS_METHODS = ("DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT")
EXPLICIT_CORS_HEADERS = ("Accept", "Authorization", "Content-Type", "Last-Event-ID", "X-Requested-With")

# In-memory login attempt tracker to reduce brute-force exposure per worker
_login_attempts: defaultdict[str, deque[float]] = defaultdict(deque)
_attempt_lock = asyncio.Lock()


def _parse_cors_origins() -> list[str]:
    value = os.getenv("YUXI_CORS_ORIGINS")
    origins = [origin.strip() for origin in (value or "").split(",") if origin.strip()]
    if origins:
        return origins

    environment = (os.getenv("YUXI_ENV") or "development").strip().lower()
    if environment in {"production", "prod"}:
        return []

    return list(DEFAULT_DEVELOPMENT_CORS_ORIGINS)


def _build_cors_options(origins: list[str] | None = None) -> dict[str, object]:
    allow_origins = _parse_cors_origins() if origins is None else origins
    if "*" in allow_origins:
        return {
            "allow_origins": ["*"],
            "allow_credentials": False,
            "allow_methods": ["*"],
            "allow_headers": ["*"],
        }

    return {
        "allow_origins": allow_origins,
        "allow_credentials": True,
        "allow_methods": list(EXPLICIT_CORS_METHODS),
        "allow_headers": list(EXPLICIT_CORS_HEADERS),
        "expose_headers": ["Content-Disposition", "X-Lock-Remaining"],
    }


app = FastAPI(
    title="超级楚楚开放 API",
    summary="超级楚楚智能体、知识库与平台管理接口",
    description=(
        "超级楚楚后端 API。第三方系统通过 `Authorization: Bearer yxkey_...` 调用。\n\n"
        "知识库自动同步请按以下顺序执行：上传文件 -> 提交文档处理任务 -> 查询任务状态 -> "
        "查询文档或执行检索验证。上传成功仅表示原始文件已保存，不表示知识已经可检索。"
    ),
    version=get_version(),
    lifespan=lifespan,
    openapi_tags=[
        {
            "name": "knowledge",
            "description": (
                "知识库管理、文件上传、解析、向量入库与检索。三方同步的核心流程是 "
                "`POST /api/knowledge/files/upload` -> "
                "`POST /api/knowledge/databases/{kb_id}/documents` -> "
                "`GET /api/tasks/{task_id}`。"
            ),
        },
        {
            "name": "tasks",
            "description": "查询知识解析和入库等后台任务的进度与最终结果。",
        },
        {
            "name": "user",
            "description": "用户配置与 API Key 管理。API Key 完整 secret 仅在创建时返回一次。",
        },
    ],
)
# 所有业务接口统一挂载到 /api，具体分组在 server.routers 中集中注册。
app.include_router(router, prefix="/api")

# CORS 设置
app.add_middleware(
    CORSMiddleware,
    **_build_cors_options(),
)


def _extract_client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


class LoginRateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        normalized_path = request.url.path.rstrip("/") or "/"
        request_signature = (normalized_path, request.method.upper())

        if request_signature in RATE_LIMIT_ENDPOINTS:
            client_ip = _extract_client_ip(request)
            now = time.monotonic()

            async with _attempt_lock:
                attempt_history = _login_attempts[client_ip]

                while attempt_history and now - attempt_history[0] > RATE_LIMIT_WINDOW_SECONDS:
                    attempt_history.popleft()

                if len(attempt_history) >= RATE_LIMIT_MAX_ATTEMPTS:
                    retry_after = int(max(1, RATE_LIMIT_WINDOW_SECONDS - (now - attempt_history[0])))
                    return JSONResponse(
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        content={"detail": "登录尝试过于频繁，请稍后再试"},
                        headers={"Retry-After": str(retry_after)},
                    )

                attempt_history.append(now)

            response = await call_next(request)

            if response.status_code < 400:
                async with _attempt_lock:
                    _login_attempts.pop(client_ip, None)

            return response

        return await call_next(request)


# 添加访问日志中间件（记录请求处理时间）
app.add_middleware(AccessLogMiddleware)

# 添加登录限流中间件
app.add_middleware(LoginRateLimitMiddleware)

if __name__ == "__main__":
    # uvicorn.run(app, host="0.0.0.0", port=5050, threads=10, workers=10, reload=True)

    uvicorn.run(
        "server.main:app",
        host="0.0.0.0",
        port=5050,
        reload=True,
        # 与 docker-compose 开发环境保持一致，避免 package 下代码变更不触发热重载。
        reload_dirs=["server", "package"],
    )
