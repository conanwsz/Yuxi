import re
from yuxi.utils import logger

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status, UploadFile, File
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Any, Literal

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import (
    APIKey,
    AgentEnv,
    CLIAuthSession,
    Department,
    DepartmentAdminAssignment,
    ExternalIdentity,
    OperationLog,
    User,
    UserConfig,
    UserDepartmentMembership,
)
from yuxi.repositories.user_repository import UserRepository
from yuxi.repositories.department_repository import DepartmentRepository
from yuxi.repositories.role_repository import RoleRepository
from server.utils.auth_middleware import (
    get_db,
    get_required_user,
    require_permission,
)
from yuxi.utils.auth_utils import AuthUtils
from yuxi.services.user_identity_service import generate_unique_uid, validate_username, is_valid_phone_number
from yuxi.services.operation_log_service import log_operation
from yuxi.services.auth_service import (
    CLI_AUTH_POLL_INTERVAL_SECONDS,
    CLI_AUTH_SESSION_TTL_SECONDS,
    CLIAuthError,
    approve_cli_auth_session,
    create_cli_auth_session,
    exchange_cli_auth_token,
    get_cli_auth_session_for_user,
)
from yuxi.storage.minio import upload_image_to_minio
from yuxi.utils.datetime_utils import utc_now_naive
from yuxi.services.permission_service import has_permission, resolve_user_permissions
from yuxi.services.organization_scope_service import user_can_manage_department
from yuxi.services.organization_service import OrganizationService

# OIDC 认证相关导入
from yuxi.services.oidc_service import (
    get_oidc_config_handler,
    oidc_callback_handler,
    oidc_exchange_code_handler,
    oidc_login_url_handler,
)

# 创建路由器
auth = APIRouter(prefix="/auth", tags=["authentication"])
TokenQuotaMode = Literal["inherit", "custom", "unlimited"]


# 请求和响应模型
class Token(BaseModel):
    access_token: str
    token_type: str
    user_id: int
    username: str
    uid: str  # 用于登录的user_id
    phone_number: str | None = None
    avatar: str | None = None
    role: str
    role_name: str
    permissions: list[str]
    department_id: int | None = None
    department_name: str | None = None


class UserCreate(BaseModel):
    username: str
    password: str
    role: str = "user"
    department_id: int | None = None
    primary_department_id: int | None = None
    part_time_department_ids: list[int] = Field(default_factory=list)
    token_quota_mode: TokenQuotaMode | None = None
    weekly_token_quota: int | None = Field(default=None, ge=0)


class UserUpdate(BaseModel):
    username: str | None = None
    password: str | None = None
    role: str | None = None
    phone_number: str | None = None
    avatar: str | None = None
    department_id: int | None = None
    primary_department_id: int | None = None
    part_time_department_ids: list[int] | None = None
    token_quota_mode: TokenQuotaMode | None = None
    weekly_token_quota: int | None = Field(default=None, ge=0)


class UserProfileUpdate(BaseModel):
    username: str | None = None
    phone_number: str | None = None


class UserResponse(BaseModel):
    id: int
    username: str
    uid: str
    phone_number: str | None = None
    avatar: str | None = None
    role: str
    role_name: str
    permissions: list[str] = Field(default_factory=list)
    department_id: int | None = None
    department_name: str | None = None  # 部门名称
    department_path: str | None = None
    primary_department: dict | None = None
    part_time_departments: list[dict] = Field(default_factory=list)
    managed_department_ids: list[int] = Field(default_factory=list)
    token_quota_mode: TokenQuotaMode | None = None
    weekly_token_quota: int | None = None
    token_quota: dict[str, Any] | None = None
    created_at: str
    last_login: str | None = None
    is_disabled: bool = False


class UserAccessOption(BaseModel):
    uid: str
    username: str
    role: str
    department_id: int | None = None
    department_name: str | None = None
    department_path: str | None = None


class ManagedDepartmentsUpdate(BaseModel):
    department_ids: list[int] = Field(default_factory=list)


class InitializeAdmin(BaseModel):
    uid: str  # 直接输入用户ID
    password: str
    phone_number: str | None = None


class UsernameValidation(BaseModel):
    username: str


class UidGeneration(BaseModel):
    username: str
    uid: str
    is_available: bool


class OIDCConfigResponse(BaseModel):
    """OIDC 配置响应"""

    enabled: bool
    login_url: str | None = None
    provider_name: str | None = "OIDC登录"


class OIDCLoginResponse(BaseModel):
    """OIDC 登录响应"""

    access_token: str
    token_type: str
    user_id: int
    username: str
    uid: str
    phone_number: str | None = None
    avatar: str | None = None
    role: str
    department_id: int | None = None
    department_name: str | None = None
    redirect_path: str = "/"


class CLIAuthSessionCreate(BaseModel):
    key_name: str | None = Field(default=None, max_length=100)


class CLIAuthTokenRequest(BaseModel):
    device_code: str


class CLIAuthSessionCreateResponse(BaseModel):
    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int


class CLIAuthSessionResponse(BaseModel):
    user_code: str
    status: str
    key_name: str
    created_at: str
    expires_at: str
    approved_at: str | None = None


class CLIAuthApproveResponse(BaseModel):
    user_code: str
    status: str
    approved_at: str | None = None


class CLIAuthTokenResponse(BaseModel):
    api_key: dict
    secret: str
    user: dict


# =============================================================================
# === 工具函数 ===
# =============================================================================


def _raise_cli_auth_error(exc: CLIAuthError) -> None:
    raise HTTPException(
        status_code=exc.status_code,
        detail={"error": exc.code, "message": exc.message},
    ) from exc


def _validate_user_token_quota_fields(
    *,
    token_quota_mode: TokenQuotaMode | None,
    weekly_token_quota: int | None,
    current_mode: TokenQuotaMode | None = None,
) -> tuple[TokenQuotaMode, int | None] | None:
    if token_quota_mode is None and weekly_token_quota is None:
        return None

    effective_mode = token_quota_mode or current_mode
    if effective_mode is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="设置 weekly_token_quota 前必须先指定 token_quota_mode",
        )
    if effective_mode == "custom":
        if weekly_token_quota is None and token_quota_mode == "custom":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="自定义额度模式必须提供 weekly_token_quota",
            )
        return effective_mode, weekly_token_quota
    if weekly_token_quota is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="仅自定义额度模式允许设置 weekly_token_quota",
        )
    return effective_mode, None


async def _get_user_token_quota_status(db: AsyncSession, user: User) -> dict[str, Any]:
    from yuxi.services.token_quota_service import get_user_token_quota_status

    return await get_user_token_quota_status(db, user)


async def _batch_get_user_token_quota_statuses(db: AsyncSession, users: list[User]) -> dict[int, dict[str, Any]]:
    from yuxi.services.token_quota_service import batch_get_user_token_quota_statuses

    return await batch_get_user_token_quota_statuses(db, users)


async def get_user_token_quota_payload(
    db: AsyncSession,
    user: User,
    *,
    token_quota: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if token_quota is None:
        token_quota = await _get_user_token_quota_status(db, user)
    return {
        "token_quota_mode": user.token_quota_mode,
        "weekly_token_quota": user.weekly_token_quota,
        "token_quota": token_quota,
    }


async def _apply_user_token_quota_fields(
    user: User,
    *,
    token_quota_mode: TokenQuotaMode,
    weekly_token_quota: int | None,
) -> None:
    user.token_quota_mode = token_quota_mode
    user.weekly_token_quota = weekly_token_quota


# 路由：登录获取令牌
# =============================================================================
# === 认证分组 ===
# =============================================================================


@auth.post("/token", response_model=Token)
async def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    # 查找用户 - 支持user_id和phone_number登录
    login_identifier = form_data.username  # OAuth2表单中的username字段作为登录标识符

    # 尝试通过user_id查找
    result = await db.execute(select(User).filter(User.uid == login_identifier))
    user = result.scalar_one_or_none()

    # 如果通过user_id没找到，尝试通过phone_number查找
    if not user:
        result = await db.execute(select(User).filter(User.phone_number == login_identifier))
        user = result.scalar_one_or_none()

    # 如果用户不存在，为防止用户名枚举攻击，返回通用错误信息
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登录标识或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 检查用户是否已被删除
    if user.is_deleted:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="该账户已注销",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 检查用户是否处于登录锁定状态
    if user.is_login_locked():
        remaining_time = user.get_remaining_lock_time()
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail=f"登录被锁定，请等待 {remaining_time} 秒后再试",
            headers={"WWW-Authenticate": "Bearer", "X-Lock-Remaining": str(remaining_time)},
        )

    # 验证密码
    if not AuthUtils.verify_password(user.password_hash, form_data.password):
        # 密码错误，增加失败次数
        user.increment_failed_login()
        await db.commit()

        # 记录失败操作
        await log_operation(db, user.id if user else None, "登录失败", f"密码错误，失败次数: {user.login_failed_count}")

        # 检查是否需要锁定
        if user.is_login_locked():
            remaining_time = user.get_remaining_lock_time()
            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail=f"由于多次登录失败，账户已被锁定 {remaining_time} 秒",
                headers={"WWW-Authenticate": "Bearer", "X-Lock-Remaining": str(remaining_time)},
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="用户名或密码错误",
                headers={"WWW-Authenticate": "Bearer"},
            )

    # 登录成功，重置失败计数器
    user.reset_failed_login()
    user.last_login = utc_now_naive()
    await db.commit()

    # 生成访问令牌
    token_data = {"sub": str(user.id)}
    access_token = AuthUtils.create_access_token(token_data)

    # 记录登录操作
    await log_operation(db, user.id, "登录")

    # 获取部门名称
    department_name = None
    if user.department_id:
        result = await db.execute(select(Department.name).filter(Department.id == user.department_id))
        department_name = result.scalar_one_or_none()

    await resolve_user_permissions(db, user)
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user_id": user.id,
        "username": user.username,
        "uid": user.uid,
        "phone_number": user.phone_number,
        "avatar": user.avatar,
        "role": user.role,
        "role_name": user.role_name,
        "permissions": sorted(user.permission_keys),
        "department_id": user.department_id,
        "department_name": department_name,
    }


# =============================================================================
# === CLI 浏览器登录授权分组 ===
# =============================================================================


@auth.post("/cli/sessions", response_model=CLIAuthSessionCreateResponse)
async def create_cli_session(data: CLIAuthSessionCreate, db: AsyncSession = Depends(get_db)):
    session, device_code = await create_cli_auth_session(db, key_name=data.key_name)
    return CLIAuthSessionCreateResponse(
        device_code=device_code,
        user_code=session.user_code,
        verification_uri="/auth/cli/authorize",
        expires_in=CLI_AUTH_SESSION_TTL_SECONDS,
        interval=CLI_AUTH_POLL_INTERVAL_SECONDS,
    )


@auth.get("/cli/sessions/{user_code}", response_model=CLIAuthSessionResponse)
async def get_cli_session(
    user_code: str,
    _current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        session = await get_cli_auth_session_for_user(db, user_code)
    except CLIAuthError as exc:
        _raise_cli_auth_error(exc)
    return CLIAuthSessionResponse(**session.to_dict())


@auth.post("/cli/sessions/{user_code}/approve", response_model=CLIAuthApproveResponse)
async def approve_cli_session(
    user_code: str,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        session = await approve_cli_auth_session(db, user_code, current_user)
    except CLIAuthError as exc:
        _raise_cli_auth_error(exc)
    return CLIAuthApproveResponse(**session.to_dict())


@auth.post("/cli/sessions/token", response_model=CLIAuthTokenResponse)
async def exchange_cli_session_token(data: CLIAuthTokenRequest, db: AsyncSession = Depends(get_db)):
    try:
        return await exchange_cli_auth_token(db, data.device_code)
    except CLIAuthError as exc:
        _raise_cli_auth_error(exc)


# 路由：校验是否需要初始化管理员
@auth.get("/check-first-run")
async def check_first_run():
    is_first_run = await pg_manager.async_check_first_run()
    return {"first_run": is_first_run}


# 路由：初始化管理员账户
@auth.post("/initialize", response_model=Token)
async def initialize_admin(admin_data: InitializeAdmin, db: AsyncSession = Depends(get_db)):
    # 检查是否是首次运行
    if not await pg_manager.async_check_first_run():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="系统已经初始化，无法再次创建初始管理员",
        )

    # 创建管理员账户
    hashed_password = AuthUtils.hash_password(admin_data.password)

    # 验证用户ID格式（只支持字母数字和下划线）
    if not re.match(r"^[a-zA-Z0-9_]+$", admin_data.uid):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="用户ID只能包含字母、数字和下划线",
        )

    if len(admin_data.uid) < 3 or len(admin_data.uid) > 20:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="用户ID长度必须在3-20个字符之间",
        )

    # 验证手机号格式（如果提供了）
    if admin_data.phone_number and not is_valid_phone_number(admin_data.phone_number):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="手机号格式不正确")

    # 由于是首次初始化，直接使用输入的user_id
    uid = admin_data.uid

    # 创建默认部门
    dept_repo = DepartmentRepository()
    default_department = await dept_repo.create(
        {
            "name": "默认部门",
            "description": "系统初始化时创建的默认部门",
        }
    )

    # 创建管理员用户
    user_repo = UserRepository()
    new_admin = await user_repo.create(
        {
            "username": admin_data.uid,
            "uid": uid,
            "phone_number": admin_data.phone_number,
            "avatar": None,
            "password_hash": hashed_password,
            "role": "superadmin",
            "department_id": default_department.id,
            "last_login": utc_now_naive(),
        }
    )

    # 生成访问令牌
    token_data = {"sub": str(new_admin.id)}
    access_token = AuthUtils.create_access_token(token_data)

    # 记录操作
    await log_operation(db, new_admin.id, "系统初始化", "创建超级管理员账户")

    await resolve_user_permissions(db, new_admin)
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user_id": new_admin.id,
        "username": new_admin.username,
        "uid": new_admin.uid,
        "phone_number": new_admin.phone_number,
        "avatar": new_admin.avatar,
        "role": new_admin.role,
        "role_name": new_admin.role_name,
        "permissions": sorted(new_admin.permission_keys),
    }


# 路由：获取当前用户信息
# =============================================================================
# === 用户信息分组 ===
# =============================================================================


async def _serialize_user(
    db: AsyncSession,
    user: User,
    *,
    role_name: str | None = None,
    token_quota: dict[str, Any] | None = None,
) -> dict:
    data = user.to_dict()
    data["is_disabled"] = bool(data.pop("is_deleted", 0))
    if role_name is None:
        role = await RoleRepository(db).get(user.role)
        role_name = role.name if role else user.role
    data["role_name"] = role_name
    memberships = await OrganizationService.user_memberships(db, user.id)
    primary = next((item for item in memberships if item["membership_type"] == "primary"), None)
    part_time = [item for item in memberships if item["membership_type"] == "part_time"]
    if primary:
        data["department_id"] = primary["department_id"]
        data["department_name"] = primary["name"]
        data["department_path"] = primary["path_label"]
    else:
        data["department_name"] = None
        data["department_path"] = None
    data["primary_department"] = primary
    data["part_time_departments"] = part_time
    data["managed_department_ids"] = await OrganizationService.admin_assignments(db, user.id)
    data.update(await get_user_token_quota_payload(db, user, token_quota=token_quota))
    return data


async def _serialize_users(db: AsyncSession, users: list[User]) -> list[dict]:
    if not users:
        return []
    user_ids = [user.id for user in users]
    token_quota_statuses = await _batch_get_user_token_quota_statuses(db, users)
    departments = {item["id"]: item for item in await OrganizationService.list_departments(db)}
    users_by_id = {user.id: user for user in users}
    membership_result = await db.execute(
        select(UserDepartmentMembership).where(UserDepartmentMembership.user_id.in_(user_ids))
    )
    membership_map: dict[int, list] = {}
    for membership in membership_result.scalars().all():
        if membership.status == "active" or users_by_id[membership.user_id].is_deleted:
            membership_map.setdefault(membership.user_id, []).append(membership)
    assignment_result = await db.execute(
        select(
            DepartmentAdminAssignment.user_id,
            DepartmentAdminAssignment.department_id,
            DepartmentAdminAssignment.status,
        ).where(DepartmentAdminAssignment.user_id.in_(user_ids))
    )
    assignment_map: dict[int, list[int]] = {}
    for user_id, department_id, assignment_status in assignment_result.all():
        if assignment_status == "active" or users_by_id[user_id].is_deleted:
            assignment_map.setdefault(int(user_id), []).append(int(department_id))
    role_names = {role.key: role.name for role in await RoleRepository(db).list_all()}

    result: list[dict] = []
    for user in users:
        data = user.to_dict()
        data["is_disabled"] = bool(data.pop("is_deleted", 0))
        data["role_name"] = role_names.get(user.role, user.role)
        relations = []
        for membership in membership_map.get(user.id, []):
            department = departments.get(membership.department_id, {})
            relations.append(
                {
                    "department_id": membership.department_id,
                    "name": department.get("name"),
                    "path": department.get("path", []),
                    "path_label": department.get("path_label"),
                    "membership_type": membership.membership_type,
                }
            )
        primary = next((item for item in relations if item["membership_type"] == "primary"), None)
        data["primary_department"] = primary
        data["part_time_departments"] = [item for item in relations if item["membership_type"] == "part_time"]
        data["department_name"] = primary["name"] if primary else None
        data["department_path"] = primary["path_label"] if primary else None
        data["managed_department_ids"] = assignment_map.get(user.id, [])
        data.update(await get_user_token_quota_payload(db, user, token_quota=token_quota_statuses[user.id]))
        result.append(data)
    return result


@auth.get("/me", response_model=UserResponse)
async def read_users_me(current_user: User = Depends(get_required_user), db: AsyncSession = Depends(get_db)):
    """获取当前登录用户的个人信息"""
    return await _serialize_user(db, current_user)


@auth.get("/me/management-scope")
async def read_my_management_scope(
    current_user: User = Depends(require_permission("departments.read")),
    db: AsyncSession = Depends(get_db),
):
    department_ids = sorted(getattr(current_user, "managed_department_ids", set()))
    departments = await OrganizationService.list_departments(db)
    return {
        "department_ids": department_ids,
        "departments": [item for item in departments if item["id"] in department_ids],
    }


# 路由：更新个人资料
@auth.put("/profile", response_model=UserResponse)
async def update_profile(
    profile_data: UserProfileUpdate,
    request: Request,
    current_user: User = Depends(get_required_user),
    db: AsyncSession = Depends(get_db),
):
    """更新当前用户的个人资料"""
    update_details = []

    # 更新用户名（仅允许修改显示名，不修改 user_id）
    if profile_data.username is not None:
        # 验证用户名格式
        is_valid, error_msg = validate_username(profile_data.username)
        if not is_valid:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=error_msg,
            )

        # 检查用户名是否已被其他用户使用
        result = await db.execute(
            select(User).filter(User.username == profile_data.username, User.id != current_user.id)
        )
        existing_user = result.scalar_one_or_none()
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="用户名已存在",
            )

        current_user.username = profile_data.username
        update_details.append(f"用户名: {profile_data.username}")

    # 更新手机号
    if profile_data.phone_number is not None:
        # 如果手机号不为空，验证格式
        if profile_data.phone_number and not is_valid_phone_number(profile_data.phone_number):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="手机号格式不正确")

        # 检查手机号是否已被其他用户使用
        if profile_data.phone_number:
            result = await db.execute(
                select(User).filter(User.phone_number == profile_data.phone_number, User.id != current_user.id)
            )
            existing_phone = result.scalar_one_or_none()
            if existing_phone:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="手机号已被其他用户使用")

        current_user.phone_number = profile_data.phone_number
        update_details.append(f"手机号: {profile_data.phone_number or '已清空'}")

    await db.commit()

    # 记录操作
    if update_details:
        await log_operation(db, current_user.id, "更新个人资料", f"更新个人资料: {', '.join(update_details)}", request)

    return await _serialize_user(db, current_user)


# 路由：创建新用户（管理员权限）
# =============================================================================
# === 用户管理分组 ===
# =============================================================================


@auth.post("/users", response_model=UserResponse)
async def create_user(
    user_data: UserCreate,
    request: Request,
    current_user: User = Depends(require_permission("users.create")),
    db: AsyncSession = Depends(get_db),
):
    """创建新用户（管理员权限）"""
    is_valid, error_msg = validate_username(user_data.username)
    if not is_valid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error_msg)
    if (await db.execute(select(User.id).where(User.username == user_data.username))).scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="用户名已存在")
    existing_uids = list((await db.execute(select(User.uid))).scalars().all())
    uid = generate_unique_uid(user_data.username, existing_uids)
    role = await RoleRepository(db).get(user_data.role)
    if not role:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="角色不存在")
    if user_data.role == "superadmin":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="不能创建超级管理员账户")
    if current_user.role != "superadmin" and user_data.role != "user":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="管理员只能创建普通用户账户",
        )
    if (user_data.token_quota_mode is not None or user_data.weekly_token_quota is not None) and not has_permission(
        current_user, "users.quota.manage"
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="缺少权限: users.quota.manage",
        )
    normalized_token_quota = _validate_user_token_quota_fields(
        token_quota_mode=user_data.token_quota_mode,
        weekly_token_quota=user_data.weekly_token_quota,
    )

    requested_primary = user_data.primary_department_id or user_data.department_id
    if current_user.role == "superadmin":
        if requested_primary is None:
            default_department = await db.get(Department, 1)
            if default_department is None:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="默认部门不存在")
            requested_primary = default_department.id
    else:
        requested_primary = requested_primary or current_user.department_id
        target_ids = [requested_primary, *user_data.part_time_department_ids]
        if requested_primary is None or any(
            not user_can_manage_department(current_user, int(department_id)) for department_id in target_ids
        ):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="只能在授权管理的组织范围内创建用户")

    new_user = User(
        username=user_data.username,
        uid=uid,
        phone_number=None,
        password_hash=AuthUtils.hash_password(user_data.password),
        role=user_data.role,
        department_id=requested_primary,
    )
    db.add(new_user)
    await db.flush()
    if normalized_token_quota is not None:
        token_quota_mode, weekly_token_quota = normalized_token_quota
        await _apply_user_token_quota_fields(
            new_user,
            token_quota_mode=token_quota_mode,
            weekly_token_quota=weekly_token_quota,
        )
    try:
        await OrganizationService.set_user_memberships(
            db,
            user=new_user,
            primary_department_id=int(requested_primary),
            part_time_department_ids=user_data.part_time_department_ids,
        )
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    log_details = [f"创建用户: {user_data.username}", f"角色: {user_data.role}"]
    if normalized_token_quota is not None:
        token_quota_mode, weekly_token_quota = normalized_token_quota
        log_details.append(f"额度模式: {token_quota_mode}")
        if weekly_token_quota is not None:
            log_details.append(f"周额度: {weekly_token_quota}")

    await log_operation(db, current_user.id, "创建用户", ", ".join(log_details), request)
    return await _serialize_user(
        db,
        new_user,
        role_name=role.name,
    )


# 路由：获取所有用户（管理员权限）
@auth.get("/users", response_model=list[UserResponse])
async def read_users(
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(require_permission("users.read")),
    db: AsyncSession = Depends(get_db),
):
    user_repo = UserRepository()

    # 部门隔离逻辑
    if current_user.role == "superadmin":
        users_with_dept = await user_repo.list_with_department(skip=skip, limit=limit, include_disabled=True)
    else:
        users_with_dept = await user_repo.list_with_department(
            skip=skip,
            limit=limit,
            department_ids=set(getattr(current_user, "managed_department_ids", set())),
            include_disabled=True,
        )
    return await _serialize_users(db, [user for user, _ in users_with_dept])


async def _ensure_user_in_current_department(db: AsyncSession, current_user: User, target_user: User) -> None:
    if current_user.role == "superadmin":
        return
    membership_query = select(UserDepartmentMembership.id).where(
        UserDepartmentMembership.user_id == target_user.id,
        UserDepartmentMembership.department_id.in_(set(getattr(current_user, "managed_department_ids", set()))),
    )
    if not target_user.is_deleted:
        membership_query = membership_query.where(UserDepartmentMembership.status == "active")
    manageable_membership = await db.execute(membership_query.limit(1))
    if manageable_membership.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="只能管理授权组织范围内的用户",
        )


@auth.get("/users/access-options", response_model=list[UserAccessOption])
async def read_user_access_options(
    skip: int = 0,
    limit: int = 1000,
    current_user: User = Depends(require_permission("users.read")),
    db: AsyncSession = Depends(get_db),
):
    user_repo = UserRepository()
    if current_user.role == "superadmin":
        users_with_dept = await user_repo.list_with_department(skip=skip, limit=limit)
    else:
        users_with_dept = await user_repo.list_with_department(
            skip=skip,
            limit=limit,
            department_ids=set(getattr(current_user, "managed_department_ids", set())),
        )
    serialized = await _serialize_users(db, [user for user, _ in users_with_dept])
    return [
        {
            "uid": item["uid"],
            "username": item["username"],
            "role": item["role"],
            "department_id": item["department_id"],
            "department_name": item["department_name"],
            "department_path": item["department_path"],
        }
        for item in serialized
    ]


# 路由：获取特定用户信息（管理员权限）
@auth.get("/users/{user_id}", response_model=UserResponse)
async def read_user(
    user_id: int,
    current_user: User = Depends(require_permission("users.read")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).filter(User.id == user_id, User.is_deleted == 0))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在",
        )
    await _ensure_user_in_current_department(db, current_user, user)
    return await _serialize_user(db, user)


@auth.get("/users/{user_id}/managed-departments")
async def read_managed_departments(
    user_id: int,
    current_user: User = Depends(require_permission("users.read")),
    db: AsyncSession = Depends(get_db),
):
    target = await db.get(User, user_id)
    if target is None or target.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    await _ensure_user_in_current_department(db, current_user, target)
    department_ids = await OrganizationService.admin_assignments(db, target.id)
    departments = await OrganizationService.list_departments(db)
    return {
        "department_ids": department_ids,
        "departments": [item for item in departments if item["id"] in department_ids],
    }


@auth.put("/users/{user_id}/managed-departments")
async def update_managed_departments(
    user_id: int,
    payload: ManagedDepartmentsUpdate,
    request: Request,
    current_user: User = Depends(require_permission("users.update")),
    db: AsyncSession = Depends(get_db),
):
    if current_user.role != "superadmin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="只有超级管理员可以配置组织管理范围")
    target = await db.get(User, user_id)
    if target is None or target.is_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    try:
        await OrganizationService.set_admin_assignments(
            db,
            user=target,
            department_ids=payload.department_ids,
            granted_by=current_user.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    await log_operation(
        db,
        current_user.id,
        "更新组织管理范围",
        f"用户 {target.uid} 管理节点: {sorted(set(payload.department_ids))}",
        request,
    )
    return {"department_ids": await OrganizationService.admin_assignments(db, target.id)}


# 路由：更新用户信息（管理员权限）
@auth.put("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: int,
    user_data: UserUpdate,
    request: Request,
    current_user: User = Depends(require_permission("users.update")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).filter(User.id == user_id, User.is_deleted == 0))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在",
        )

    await _ensure_user_in_current_department(db, current_user, user)

    # 检查权限
    if user.role == "superadmin" and current_user.role != "superadmin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="只有超级管理员才能修改超级管理员账户",
        )

    # 超级管理员账户不能被降级（只能由其他超级管理员修改）
    if user.role == "superadmin" and user_data.role and user_data.role != "superadmin" and current_user.id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="不能降级超级管理员账户",
        )

    if current_user.role != "superadmin" and user_data.role is not None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="只有超级管理员才能修改用户角色")
    if (
        "token_quota_mode" in user_data.model_fields_set or "weekly_token_quota" in user_data.model_fields_set
    ) and not has_permission(current_user, "users.quota.manage"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="缺少权限: users.quota.manage",
        )

    # 更新信息
    update_details = []

    if user_data.username is not None:
        # 检查用户名是否已被其他用户使用
        result = await db.execute(select(User).filter(User.username == user_data.username, User.id != user_id))
        existing_user = result.scalar_one_or_none()
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="用户名已存在",
            )
        user.username = user_data.username
        update_details.append(f"用户名: {user_data.username}")

    if user_data.password is not None:
        user.password_hash = AuthUtils.hash_password(user_data.password)
        update_details.append("密码已更新")

    if user_data.role is not None:
        if not await RoleRepository(db).get(user_data.role):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="角色不存在")
        if user_data.role == "superadmin" and user.role != "superadmin":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="不能新增超级管理员账户")
        old_role = user.role
        user.role = user_data.role
        update_details.append(f"角色: {old_role} -> {user_data.role}")

    if user_data.phone_number is not None:
        user.phone_number = user_data.phone_number
        update_details.append(f"手机号: {user_data.phone_number or '已清空'}")

    if user_data.avatar is not None:
        user.avatar = user_data.avatar
        update_details.append(f"头像: {user_data.avatar or '已清空'}")

    if "token_quota_mode" in user_data.model_fields_set or "weekly_token_quota" in user_data.model_fields_set:
        normalized_token_quota = _validate_user_token_quota_fields(
            token_quota_mode=user_data.token_quota_mode,
            weekly_token_quota=user_data.weekly_token_quota,
            current_mode=user.token_quota_mode,
        )
        if normalized_token_quota is not None:
            token_quota_mode, weekly_token_quota = normalized_token_quota
            old_token_quota_mode = user.token_quota_mode
            old_weekly_token_quota = user.weekly_token_quota
            await _apply_user_token_quota_fields(
                user,
                token_quota_mode=token_quota_mode,
                weekly_token_quota=weekly_token_quota,
            )
            if old_token_quota_mode != token_quota_mode:
                update_details.append(f"额度模式: {old_token_quota_mode} -> {token_quota_mode}")
            if old_weekly_token_quota != weekly_token_quota:
                update_details.append(
                    f"周额度: {old_weekly_token_quota if old_weekly_token_quota is not None else '未设置'}"
                    f" -> {weekly_token_quota if weekly_token_quota is not None else '未设置'}"
                )

    membership_fields = user_data.model_fields_set & {
        "department_id",
        "primary_department_id",
        "part_time_department_ids",
    }
    if membership_fields:
        existing_memberships = await OrganizationService.user_memberships(db, user.id)
        existing_part_time_ids = [
            item["department_id"] for item in existing_memberships if item["membership_type"] == "part_time"
        ]
        primary_department_id = (
            user_data.primary_department_id
            if "primary_department_id" in user_data.model_fields_set
            else user_data.department_id
            if "department_id" in user_data.model_fields_set
            else user.department_id
        )
        part_time_department_ids = (
            user_data.part_time_department_ids
            if "part_time_department_ids" in user_data.model_fields_set
            else existing_part_time_ids
        )
        if primary_department_id is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="用户必须设置主部门")
        if current_user.role != "superadmin" and any(
            not user_can_manage_department(current_user, int(department_id))
            for department_id in [primary_department_id, *(part_time_department_ids or [])]
        ):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="只能分配授权管理范围内的组织")
        try:
            await OrganizationService.set_user_memberships(
                db,
                user=user,
                primary_department_id=int(primary_department_id),
                part_time_department_ids=part_time_department_ids or [],
            )
        except ValueError as exc:
            await db.rollback()
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        update_details.append(f"主部门ID: {primary_department_id}")

    await db.commit()

    # 记录操作
    await log_operation(db, current_user.id, "更新用户", f"更新用户ID {user_id}: {', '.join(update_details)}", request)

    return await _serialize_user(
        db,
        user,
    )


# 路由：禁用用户（管理员权限）
@auth.post("/users/{user_id}/disable", response_model=dict)
async def disable_user(
    user_id: int,
    request: Request,
    current_user: User = Depends(require_permission("users.disable")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).filter(User.id == user_id, User.is_deleted == 0))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在",
        )

    await _ensure_user_in_current_department(db, current_user, user)

    # 不能禁用超级管理员账户
    if user.role == "superadmin":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="不能禁用超级管理员账户",
        )

    if current_user.role == "admin" and user.role != "user":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="管理员只能禁用普通用户账户",
        )

    # 不能禁用自己的账户
    if user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="不能禁用自己的账户",
        )

    disable_detail = f"禁用用户: {user.username}, ID: {user.id}, 角色: {user.role}"

    user.is_deleted = 1
    user.deleted_at = utc_now_naive()
    api_key_result = await db.execute(select(APIKey).filter(APIKey.user_id == user.id))
    for api_key in api_key_result.scalars().all():
        api_key.is_enabled = False

    await db.commit()

    # 记录操作
    await log_operation(db, current_user.id, "禁用用户", disable_detail, request)

    return {"success": True, "message": "用户已禁用"}


# 路由：重新激活用户（管理员权限）
@auth.post("/users/{user_id}/activate", response_model=dict)
async def activate_user(
    user_id: int,
    request: Request,
    current_user: User = Depends(require_permission("users.enable")),
    db: AsyncSession = Depends(get_db),
):
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")

    await _ensure_user_in_current_department(db, current_user, user)
    if not user.is_deleted:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="该用户已处于激活状态")
    if user.role == "superadmin":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="超级管理员账户无需激活")
    if current_user.role == "admin" and user.role != "user":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="管理员只能激活普通用户账户")

    user.is_deleted = 0
    user.deleted_at = None

    # 兼容旧版禁用逻辑：至少恢复主部门关系和管理员的主部门管理范围。
    if user.department_id is not None:
        await db.execute(
            update(UserDepartmentMembership)
            .where(
                UserDepartmentMembership.user_id == user.id,
                UserDepartmentMembership.department_id == user.department_id,
                UserDepartmentMembership.membership_type == "primary",
            )
            .values(status="active", updated_at=utc_now_naive())
        )
        if user.role == "admin":
            await db.execute(
                update(DepartmentAdminAssignment)
                .where(
                    DepartmentAdminAssignment.user_id == user.id,
                    DepartmentAdminAssignment.department_id == user.department_id,
                )
                .values(status="active", updated_at=utc_now_naive())
            )

    await db.commit()
    await log_operation(
        db,
        current_user.id,
        "激活用户",
        f"激活用户: {user.username}, ID: {user.id}, 角色: {user.role}",
        request,
    )

    return {"success": True, "message": "用户已激活"}


# 路由：物理删除用户（管理员权限）
@auth.delete("/users/{user_id}", response_model=dict)
async def delete_user(
    user_id: int,
    request: Request,
    current_user: User = Depends(require_permission("users.delete")),
    db: AsyncSession = Depends(get_db),
):
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")

    await _ensure_user_in_current_department(db, current_user, user)
    if user.role == "superadmin":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="不能删除超级管理员账户")
    if current_user.role == "admin" and user.role != "user":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="管理员只能删除普通用户账户")
    if user.id == current_user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="不能删除自己的账户")

    deletion_detail = f"物理删除用户: {user.username}, ID: {user.id}, UID: {user.uid}, 角色: {user.role}"
    api_key_ids = list((await db.execute(select(APIKey.id).where(APIKey.user_id == user.id))).scalars().all())
    await db.execute(
        update(CLIAuthSession).where(CLIAuthSession.approved_user_id == user.id).values(approved_user_id=None)
    )
    if api_key_ids:
        await db.execute(
            update(CLIAuthSession).where(CLIAuthSession.api_key_id.in_(api_key_ids)).values(api_key_id=None)
        )
    await db.execute(
        update(DepartmentAdminAssignment).where(DepartmentAdminAssignment.granted_by == user.id).values(granted_by=None)
    )
    for model, condition in (
        (OperationLog, OperationLog.user_id == user.id),
        (APIKey, APIKey.user_id == user.id),
        (ExternalIdentity, ExternalIdentity.user_id == user.id),
        (AgentEnv, AgentEnv.uid == user.uid),
        (UserConfig, UserConfig.uid == user.uid),
        (UserDepartmentMembership, UserDepartmentMembership.user_id == user.id),
        (DepartmentAdminAssignment, DepartmentAdminAssignment.user_id == user.id),
    ):
        await db.execute(delete(model).where(condition))
    await db.delete(user)
    await db.commit()
    await log_operation(db, current_user.id, "删除用户", deletion_detail, request)

    return {"success": True, "message": "用户已删除"}


# 路由：验证用户名并生成user_id
@auth.post("/validate-username", response_model=UidGeneration)
async def validate_username_and_generate_uid(
    validation_data: UsernameValidation,
    current_user: User = Depends(require_permission("users.create")),
    db: AsyncSession = Depends(get_db),
):
    """验证用户名格式并生成可用的user_id"""
    # 验证用户名格式
    is_valid, error_msg = validate_username(validation_data.username)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error_msg,
        )

    # 检查用户名是否已存在
    result = await db.execute(select(User).filter(User.username == validation_data.username))
    existing_user = result.scalar_one_or_none()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="用户名已存在",
        )

    # 生成唯一的 uid
    result = await db.execute(select(User.uid))
    existing_uids = [uid for (uid,) in result.all()]
    uid = generate_unique_uid(validation_data.username, existing_uids)

    return UidGeneration(username=validation_data.username, uid=uid, is_available=True)


# 路由：检查 uid 是否可用
@auth.get("/check-uid/{uid}")
async def check_uid_availability(
    uid: str,
    current_user: User = Depends(require_permission("users.create")),
    db: AsyncSession = Depends(get_db),
):
    """检查 uid 是否可用"""
    result = await db.execute(select(User).filter(User.uid == uid))
    existing_user = result.scalar_one_or_none()
    return {"uid": uid, "is_available": existing_user is None}


# 路由：上传用户头像
@auth.post("/upload-avatar")
async def upload_user_avatar(
    file: UploadFile = File(...), current_user: User = Depends(get_required_user), db: AsyncSession = Depends(get_db)
):
    """上传用户头像"""
    try:
        avatar_url = await upload_image_to_minio(
            file,
            object_prefix=f"avatar/{current_user.id}",
            max_size_bytes=5 * 1024 * 1024,
            too_large_message="文件大小不能超过5MB",
        )

        current_user.avatar = avatar_url
        await db.commit()
        await log_operation(db, current_user.id, "上传头像", f"更新头像: {avatar_url}")

        return {"success": True, "avatar_url": avatar_url, "message": "头像上传成功"}

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"头像上传失败: {str(e)}")


# 路由：模拟用户登录（超级管理员专用）
@auth.post("/impersonate/{user_id}", response_model=Token)
async def impersonate_user(
    user_id: int,
    request: Request,
    current_user: User = Depends(require_permission("users.impersonate")),
    db: AsyncSession = Depends(get_db),
):
    """超级管理员模拟其他用户登录"""
    # 查找目标用户
    result = await db.execute(select(User).filter(User.id == user_id, User.is_deleted == 0))
    target_user = result.scalar_one_or_none()
    if target_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在",
        )

    # 不能模拟超级管理员
    if target_user.role == "superadmin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="不能模拟超级管理员账户",
        )

    # 生成访问令牌
    token_data = {"sub": str(target_user.id)}
    access_token = AuthUtils.create_access_token(token_data)

    # 获取部门名称
    department_name = None
    if target_user.department_id:
        result = await db.execute(select(Department.name).filter(Department.id == target_user.department_id))
        department_name = result.scalar_one_or_none()

    # 记录操作（危险操作标记）
    await log_operation(db, current_user.id, "⚠️ 危险操作-模拟用户", f"模拟用户: {target_user.username}", request)

    # 控制台警告日志
    logger.warning(f"⚠️ [危险操作] 超级管理员 {current_user.username} 模拟登录用户: {target_user.username}")

    await resolve_user_permissions(db, target_user)
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user_id": target_user.id,
        "username": target_user.username,
        "uid": target_user.uid,
        "phone_number": target_user.phone_number,
        "avatar": target_user.avatar,
        "role": target_user.role,
        "role_name": target_user.role_name,
        "permissions": sorted(target_user.permission_keys),
        "department_id": target_user.department_id,
        "department_name": department_name,
    }


# =============================================================================
# === OIDC 认证分组 ===
# =============================================================================


@auth.get("/oidc/config", response_model=OIDCConfigResponse)
async def get_oidc_config():
    """获取 OIDC 配置（供前端使用）"""
    return await get_oidc_config_handler()


@auth.get("/oidc/login-url")
async def get_oidc_login_url(redirect_path: str = "/"):
    """获取 OIDC 登录 URL"""
    return await oidc_login_url_handler(redirect_path)


@auth.get("/oidc/callback", response_class=RedirectResponse)
async def oidc_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """处理 OIDC 回调 - 重定向到前端 Vue 路由"""
    return await oidc_callback_handler(code, state, db, request, error, error_description)


@auth.post("/oidc/exchange-code", response_model=OIDCLoginResponse)
async def oidc_exchange_code(code: str = Body(..., embed=True)):
    """使用一次性 code 交换 OIDC 登录数据"""
    return await oidc_exchange_code_handler(code)
