"""用户级配置与凭据路由"""

import re
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from server.routers.auth_router import TokenQuotaMode
from server.utils.auth_middleware import get_current_user, get_db, get_required_user, require_permission
from yuxi.config import UserConfig, UserConfigSchema
from yuxi.services.token_quota_service import (
    get_user_token_quota_payload_with_breakdown,
)
from yuxi.storage.minio import upload_image_to_minio
from yuxi.storage.postgres.models_business import APIKey, AgentEnv, Department, ExternalIdentity, User
from yuxi.utils.auth_utils import AuthUtils
from yuxi.utils.datetime_utils import coerce_any_to_utc_datetime, format_utc_datetime, utc_now_naive

user_router = APIRouter(prefix="/user", tags=["user"])

ENV_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MAX_ENV_COUNT = 200
MAX_ENV_KEY_LENGTH = 128
MAX_ENV_VALUE_LENGTH = 32768
MAX_USER_IMAGE_SIZE_BYTES = 5 * 1024 * 1024


class APIKeyCreate(BaseModel):
    name: str
    user_id: int | None = None
    department_id: int | None = None
    expires_at: str | None = None


class APIKeyUpdate(BaseModel):
    name: str | None = None
    expires_at: str | None = None
    is_enabled: bool | None = None


class APIKeyResponse(BaseModel):
    id: int
    key_prefix: str
    name: str
    user_id: int
    department_id: int | None
    expires_at: str | None
    is_enabled: bool
    last_used_at: str | None
    created_by: str
    created_at: str


class APIKeyCreateResponse(BaseModel):
    api_key: APIKeyResponse
    secret: str


class AgentEnvUpdate(BaseModel):
    env: dict[str, Any] = Field(default_factory=dict)


class AgentEnvResponse(BaseModel):
    env: dict[str, str]
    readonly_keys: list[str] = Field(default_factory=list)
    updated_at: str | None = None


class UserTokenQuotaResponse(BaseModel):
    token_quota_mode: TokenQuotaMode | None = None
    weekly_token_quota: int | None = None
    token_quota: dict[str, Any] | None = None


async def get_logged_in_user(user: User | None = Depends(get_current_user)) -> User:
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="请登录后再访问",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


@user_router.get("/config", response_model=dict)
async def get_user_config(
    current_user: User = Depends(get_logged_in_user),
    db: AsyncSession = Depends(get_db),
):
    user_config = await UserConfig.load(db, current_user.uid)
    return user_config.dump_config()


@user_router.put("/config", response_model=dict)
async def update_user_config(
    data: UserConfigSchema,
    current_user: User = Depends(get_logged_in_user),
    db: AsyncSession = Depends(get_db),
):
    user_config = await UserConfig(uid=current_user.uid, schema=data).save(db)
    return user_config.dump_config()


@user_router.get("/token-quota", response_model=UserTokenQuotaResponse)
async def read_current_user_token_quota(
    current_user: User = Depends(get_logged_in_user),
    db: AsyncSession = Depends(get_db),
):
    return UserTokenQuotaResponse(**(await get_user_token_quota_payload_with_breakdown(db, current_user)))


@user_router.post("/upload-image", response_model=dict)
async def upload_user_image(file: UploadFile = File(...), current_user: User = Depends(get_required_user)):
    try:
        image_url = await upload_image_to_minio(
            file,
            object_prefix=f"images/{current_user.uid}",
            max_size_bytes=MAX_USER_IMAGE_SIZE_BYTES,
            too_large_message="图片大小不能超过 5MB",
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return {"success": True, "image_url": image_url, "url": image_url}


def validate_agent_env(env: dict[str, Any]) -> dict[str, str]:
    if len(env) > MAX_ENV_COUNT:
        raise HTTPException(status_code=400, detail=f"环境变量数量不能超过 {MAX_ENV_COUNT} 个")

    normalized: dict[str, str] = {}
    for key, value in env.items():
        if not isinstance(key, str):
            raise HTTPException(status_code=400, detail="环境变量名必须是字符串")
        name = key.strip()
        if not name:
            raise HTTPException(status_code=400, detail="环境变量名不能为空")
        if len(name) > MAX_ENV_KEY_LENGTH:
            raise HTTPException(status_code=400, detail=f"环境变量名长度不能超过 {MAX_ENV_KEY_LENGTH}")
        if not ENV_KEY_PATTERN.match(name):
            raise HTTPException(status_code=400, detail=f"环境变量名 {name} 格式不正确")
        if name in normalized:
            raise HTTPException(status_code=400, detail=f"环境变量名 {name} 重复")
        if not isinstance(value, str):
            raise HTTPException(status_code=400, detail=f"环境变量 {name} 的值必须是字符串")
        if len(value) > MAX_ENV_VALUE_LENGTH:
            raise HTTPException(status_code=400, detail=f"环境变量 {name} 的值过长")
        normalized[name] = value
    return normalized


async def is_oidc_user(db: AsyncSession, user: User) -> bool:
    """判断用户是否绑定了任一 OIDC 外部身份。"""

    result = await db.execute(select(ExternalIdentity.id).where(ExternalIdentity.user_id == user.id).limit(1))
    return result.scalar_one_or_none() is not None


# OIDC 用户不可修改的系统级环境变量键集合；其他键允许用户自由调整。
OIDC_READONLY_ENV_KEYS: tuple[str, ...] = ("uid", "entity_code", "dept_code")


async def load_oidc_readonly_env(db: AsyncSession, user: User) -> dict[str, str]:
    """收集 OIDC 用户的系统维护环境变量。

    - uid：始终等于本地 User.uid。
    - entity_code / dept_code：取自 OIDC 用户当前部门，仅在部门表上有值时注入。
    """

    readonly: dict[str, str] = {"uid": str(user.uid)}
    if user.department_id is not None:
        result = await db.execute(
            select(Department.entity_code, Department.department_code).where(Department.id == user.department_id)
        )
        row = result.first()
        if row is not None:
            entity_code, department_code = row
            if entity_code:
                readonly["entity_code"] = entity_code
            if department_code:
                readonly["dept_code"] = department_code
    return readonly


def build_agent_env_response(
    env: dict[str, str],
    *,
    user: User,
    oidc_user: bool,
    updated_at: str | None = None,
    oidc_readonly_env: dict[str, str] | None = None,
) -> AgentEnvResponse:
    """合成用户可见环境变量，OIDC 系统字段始终以权威来源为准。"""

    visible_env = dict(env)
    readonly_keys: list[str] = []
    if oidc_user:
        readonly_source = oidc_readonly_env if oidc_readonly_env is not None else {"uid": str(user.uid)}
        readonly_keys = list(readonly_source.keys())
        for key, value in readonly_source.items():
            visible_env[key] = value
    return AgentEnvResponse(env=visible_env, readonly_keys=readonly_keys, updated_at=updated_at)


def ensure_api_key_owner(api_key: APIKey, current_user: User) -> None:
    if api_key.user_id != current_user.id and current_user.role != "superadmin":
        raise HTTPException(status_code=403, detail="无权操作此 API Key")


async def get_accessible_api_key(db: AsyncSession, api_key_id: int, current_user: User) -> APIKey:
    result = await db.execute(select(APIKey).filter(APIKey.id == api_key_id))
    api_key = result.scalar_one_or_none()
    if not api_key:
        raise HTTPException(status_code=404, detail="API Key 不存在")
    ensure_api_key_owner(api_key, current_user)
    return api_key


@user_router.get("/apikey/", response_model=dict)
async def list_api_keys(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    current_user: User = Depends(require_permission("apikey.manage")),
    db: AsyncSession = Depends(get_db),
):
    query = select(APIKey).order_by(APIKey.created_at.desc()).offset(skip).limit(limit)
    count_query = select(func.count(APIKey.id))
    if current_user.role != "superadmin":
        query = query.filter(APIKey.user_id == current_user.id)
        count_query = count_query.filter(APIKey.user_id == current_user.id)

    result = await db.execute(query)
    api_keys = result.scalars().all()
    total_result = await db.execute(count_query)

    return {
        "api_keys": [key.to_dict() for key in api_keys],
        "total": total_result.scalar(),
    }


@user_router.post("/apikey/", response_model=APIKeyCreateResponse)
async def create_api_key(
    data: APIKeyCreate,
    current_user: User = Depends(require_permission("apikey.manage")),
    db: AsyncSession = Depends(get_db),
):
    if data.user_id and data.user_id != current_user.id and current_user.role != "superadmin":
        raise HTTPException(status_code=403, detail="无权为其他用户创建 API Key")

    target_user = current_user
    if data.user_id:
        result = await db.execute(select(User).filter(User.id == data.user_id))
        user = result.scalar_one_or_none()
        if not user or user.is_deleted:
            raise HTTPException(status_code=404, detail="关联的用户不存在")
        target_user = user

    if data.department_id is not None and data.department_id != target_user.department_id:
        raise HTTPException(status_code=403, detail="API Key 部门必须与关联用户部门一致")

    full_key, key_hash, key_prefix = AuthUtils.generate_api_key()
    expires_at = None
    if data.expires_at:
        aware_dt = coerce_any_to_utc_datetime(data.expires_at)
        if aware_dt:
            expires_at = aware_dt.replace(tzinfo=None)

    api_key = APIKey(
        key_hash=key_hash,
        key_prefix=key_prefix,
        name=data.name,
        user_id=target_user.id,
        department_id=data.department_id,
        expires_at=expires_at,
        created_by=str(current_user.id),
    )

    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)

    return APIKeyCreateResponse(
        api_key=APIKeyResponse(**api_key.to_dict()),
        secret=full_key,
    )


@user_router.get("/apikey/{api_key_id}", response_model=dict)
async def get_api_key(
    api_key_id: int,
    current_user: User = Depends(require_permission("apikey.manage")),
    db: AsyncSession = Depends(get_db),
):
    api_key = await get_accessible_api_key(db, api_key_id, current_user)
    return {"api_key": api_key.to_dict()}


@user_router.put("/apikey/{api_key_id}", response_model=dict)
async def update_api_key(
    api_key_id: int,
    data: APIKeyUpdate,
    current_user: User = Depends(require_permission("apikey.manage")),
    db: AsyncSession = Depends(get_db),
):
    api_key = await get_accessible_api_key(db, api_key_id, current_user)

    if data.name is not None:
        api_key.name = data.name
    if data.expires_at is not None:
        aware_dt = coerce_any_to_utc_datetime(data.expires_at)
        api_key.expires_at = aware_dt.replace(tzinfo=None) if aware_dt else None
    if data.is_enabled is not None:
        api_key.is_enabled = data.is_enabled

    await db.commit()
    await db.refresh(api_key)
    return {"api_key": api_key.to_dict()}


@user_router.delete("/apikey/{api_key_id}", response_model=dict)
async def delete_api_key(
    api_key_id: int,
    current_user: User = Depends(require_permission("apikey.manage")),
    db: AsyncSession = Depends(get_db),
):
    api_key = await get_accessible_api_key(db, api_key_id, current_user)

    await db.delete(api_key)
    await db.commit()
    return {"success": True}


@user_router.get("/agent-env", response_model=AgentEnvResponse)
async def get_agent_env(
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(AgentEnv).filter(AgentEnv.uid == current_user.uid))
    agent_env = result.scalar_one_or_none()
    oidc_user = await is_oidc_user(db, current_user)
    oidc_readonly_env = (
        await load_oidc_readonly_env(db, current_user) if oidc_user else None
    )
    if agent_env is None:
        return build_agent_env_response(
            {},
            user=current_user,
            oidc_user=oidc_user,
            oidc_readonly_env=oidc_readonly_env,
        )
    return build_agent_env_response(
        agent_env.env or {},
        user=current_user,
        oidc_user=oidc_user,
        oidc_readonly_env=oidc_readonly_env,
        updated_at=format_utc_datetime(agent_env.updated_at),
    )


@user_router.put("/agent-env", response_model=AgentEnvResponse)
async def update_agent_env(
    data: AgentEnvUpdate,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    env = validate_agent_env(data.env)
    oidc_user = await is_oidc_user(db, current_user)
    oidc_readonly_env = (
        await load_oidc_readonly_env(db, current_user) if oidc_user else None
    )
    if oidc_user:
        for key in OIDC_READONLY_ENV_KEYS:
            if key not in env:
                continue
            requested = env[key]
            expected = (oidc_readonly_env or {}).get(key)
            if expected is None:
                # 用户当前没有这个系统级字段，提交里出现就视为非法覆盖
                raise HTTPException(
                    status_code=400,
                    detail=f"OIDC 用户环境变量 {key} 由系统维护，不能修改",
                )
            if requested != expected:
                raise HTTPException(
                    status_code=400,
                    detail=f"OIDC 用户环境变量 {key} 由系统维护，不能修改",
                )
            env.pop(key, None)

    result = await db.execute(select(AgentEnv).filter(AgentEnv.uid == current_user.uid))
    current_agent_env = result.scalar_one_or_none()
    if current_agent_env is not None and (current_agent_env.env or {}) == env:
        return build_agent_env_response(
            current_agent_env.env or {},
            user=current_user,
            oidc_user=oidc_user,
            oidc_readonly_env=oidc_readonly_env,
            updated_at=format_utc_datetime(current_agent_env.updated_at),
        )

    now = utc_now_naive()
    stmt = (
        pg_insert(AgentEnv)
        .values(uid=current_user.uid, env=env, updated_at=now)
        .on_conflict_do_update(
            index_elements=[AgentEnv.uid],
            set_={"env": env, "updated_at": now},
        )
        .returning(AgentEnv)
    )
    await db.execute(stmt)
    await db.commit()
    # 直接返回刚写入的 env/now，避免身份映射中的旧实例属性导致返回陈旧值
    return build_agent_env_response(
        env,
        user=current_user,
        oidc_user=oidc_user,
        oidc_readonly_env=oidc_readonly_env,
        updated_at=format_utc_datetime(now),
    )
