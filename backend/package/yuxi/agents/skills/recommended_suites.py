"""Recommended skill suites service layer.

管理员维护的「推荐技能套件」CRUD + 启停。套件本身只存元数据（name/provider/
description/source + members 列表），真实 skill 内容由用户走远程安装流程按需拉取。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from yuxi.agents.skills.remote_install import _normalize_source
from yuxi.agents.skills.service import is_valid_skill_slug
from yuxi.storage.postgres.models_business import (
    RecommendedSkillSuite,
    RecommendedSuiteMember,
    User,
)

MAX_NAME_LENGTH = 128
MAX_PROVIDER_LENGTH = 128
MAX_DESCRIPTION_LENGTH = 2000
MAX_SOURCE_LENGTH = 512
MIN_MEMBERS = 1
MAX_MEMBERS = 64


class RecommendedSuiteValidationError(ValueError):
    """推荐套件参数校验失败。"""


class RecommendedSuiteNotFoundError(LookupError):
    """找不到目标套件。"""


class RecommendedSuiteConflictError(ValueError):
    """slug 重复等唯一性冲突。"""


def _normalize_slug(value: Any) -> str:
    if not isinstance(value, str):
        raise RecommendedSuiteValidationError("slug 必须为字符串")
    value = value.strip()
    if not value:
        raise RecommendedSuiteValidationError("slug 不能为空")
    if not is_valid_skill_slug(value):
        raise RecommendedSuiteValidationError(f"slug 非法：{value!r}（需匹配 [a-z0-9-_]+）")
    if len(value) > 80:
        raise RecommendedSuiteValidationError("slug 长度不能超过 80")
    return value


def _normalize_short_text(value: Any, *, field: str, max_length: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise RecommendedSuiteValidationError(f"{field} 必须为字符串")
    value = value.strip()
    if not value:
        raise RecommendedSuiteValidationError(f"{field} 不能为空")
    if len(value) > max_length:
        raise RecommendedSuiteValidationError(f"{field} 长度不能超过 {max_length}")
    return value


def _normalize_description(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise RecommendedSuiteValidationError("description 必须为字符串")
    value = value.strip()
    if len(value) > MAX_DESCRIPTION_LENGTH:
        raise RecommendedSuiteValidationError(f"description 长度不能超过 {MAX_DESCRIPTION_LENGTH}")
    return value


def _normalize_source_field(value: Any) -> str:
    if not isinstance(value, str):
        raise RecommendedSuiteValidationError("source 必须为字符串")
    try:
        normalized = _normalize_source(value)
    except ValueError as exc:
        raise RecommendedSuiteValidationError(str(exc)) from exc
    if len(normalized) > MAX_SOURCE_LENGTH:
        raise RecommendedSuiteValidationError(f"source 长度不能超过 {MAX_SOURCE_LENGTH}")
    return normalized


def _normalize_sort_order(value: Any) -> int:
    if value is None or value == "":
        return 0
    if isinstance(value, bool):
        raise RecommendedSuiteValidationError("sort_order 不能为布尔值")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError as exc:
            raise RecommendedSuiteValidationError("sort_order 必须为整数") from exc
    raise RecommendedSuiteValidationError("sort_order 必须为整数")


def _normalize_enabled(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    raise RecommendedSuiteValidationError("enabled 必须为布尔值")


def _normalize_members(value: Any) -> list[dict[str, Any]]:
    if value is None:
        raise RecommendedSuiteValidationError("members 不能为空")
    if not isinstance(value, list):
        raise RecommendedSuiteValidationError("members 必须为列表")
    if len(value) < MIN_MEMBERS:
        raise RecommendedSuiteValidationError(f"members 至少 {MIN_MEMBERS} 条")
    if len(value) > MAX_MEMBERS:
        raise RecommendedSuiteValidationError(f"members 不能超过 {MAX_MEMBERS} 条")

    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, dict):
            raise RecommendedSuiteValidationError(f"members[{index}] 必须为对象")
        slug = _normalize_slug(raw.get("slug"))
        if slug in seen:
            raise RecommendedSuiteValidationError(f"members slug 重复：{slug}")
        seen.add(slug)
        name = _normalize_short_text(raw.get("name"), field=f"members[{index}].name", max_length=MAX_NAME_LENGTH)
        description = _normalize_description(raw.get("description") or "")
        sort_order = _normalize_sort_order(raw.get("sort_order", index))
        items.append(
            {
                "slug": slug,
                "name": name,
                "description": description,
                "sort_order": sort_order,
            }
        )

    # 按 sort_order 排序后再赋连续值，避免重复
    items.sort(key=lambda item: (item["sort_order"], item["slug"]))
    for idx, item in enumerate(items):
        item["sort_order"] = idx
    return items


def _normalize_suite_payload(payload: Any) -> dict[str, Any]:
    if payload is None or not isinstance(payload, dict):
        raise RecommendedSuiteValidationError("请求体必须为对象")
    return {
        "slug": _normalize_slug(payload.get("slug")),
        "name": _normalize_short_text(payload.get("name"), field="name", max_length=MAX_NAME_LENGTH),
        "provider": _normalize_short_text(payload.get("provider"), field="provider", max_length=MAX_PROVIDER_LENGTH),
        "description": _normalize_description(payload.get("description")),
        "source": _normalize_source_field(payload.get("source")),
        "sort_order": _normalize_sort_order(payload.get("sort_order")),
        "enabled": _normalize_enabled(payload.get("enabled")),
        "members": _normalize_members(payload.get("members")),
    }


def _suite_query(db: AsyncSession):
    return select(RecommendedSkillSuite).options(selectinload(RecommendedSkillSuite.members))


async def list_recommended_suites(db: AsyncSession) -> list[dict]:
    """仅返回启用的套件（用户视角）。按 sort_order, id 升序。"""
    stmt = (
        _suite_query(db)
        .where(RecommendedSkillSuite.enabled.is_(True))
        .order_by(
            RecommendedSkillSuite.sort_order.asc(),
            RecommendedSkillSuite.id.asc(),
        )
    )
    result = await db.execute(stmt)
    return [item.to_dict() for item in result.scalars().all()]


async def list_recommended_suites_admin(db: AsyncSession) -> list[dict]:
    """管理视角：含 disabled。按 sort_order, id 升序。"""
    stmt = _suite_query(db).order_by(
        RecommendedSkillSuite.sort_order.asc(),
        RecommendedSkillSuite.id.asc(),
    )
    result = await db.execute(stmt)
    return [item.to_dict() for item in result.scalars().all()]


async def get_recommended_suite(db: AsyncSession, suite_id: int) -> dict | None:
    stmt = _suite_query(db).where(RecommendedSkillSuite.id == suite_id)
    result = await db.execute(stmt)
    item = result.scalar_one_or_none()
    return item.to_dict() if item else None


async def _create_suite_internal(
    db: AsyncSession,
    *,
    payload: dict,
    operator: User,
) -> dict:
    suite = RecommendedSkillSuite(
        slug=payload["slug"],
        name=payload["name"],
        provider=payload["provider"],
        description=payload["description"],
        source=payload["source"],
        sort_order=payload["sort_order"],
        enabled=payload["enabled"],
        created_by=operator.uid if operator else None,
        updated_by=operator.uid if operator else None,
        members=[
            RecommendedSuiteMember(
                slug=m["slug"],
                name=m["name"],
                description=m["description"],
                sort_order=m["sort_order"],
            )
            for m in payload["members"]
        ],
    )
    db.add(suite)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise RecommendedSuiteConflictError(f"slug 已存在或违反唯一约束：{payload['slug']}") from exc
    await db.refresh(suite, attribute_names=["members"])
    return suite.to_dict()


async def create_recommended_suite(
    db: AsyncSession,
    *,
    payload: Any,
    operator: User,
) -> dict:
    normalized = _normalize_suite_payload(payload)
    return await _create_suite_internal(db, payload=normalized, operator=operator)


async def update_recommended_suite(
    db: AsyncSession,
    *,
    suite_id: int,
    payload: Any,
    operator: User,
) -> dict:
    normalized = _normalize_suite_payload(payload)

    stmt = _suite_query(db).where(RecommendedSkillSuite.id == suite_id)
    result = await db.execute(stmt)
    suite = result.scalar_one_or_none()
    if suite is None:
        raise RecommendedSuiteNotFoundError(f"找不到套件 id={suite_id}")

    suite.slug = normalized["slug"]
    suite.name = normalized["name"]
    suite.provider = normalized["provider"]
    suite.description = normalized["description"]
    suite.source = normalized["source"]
    suite.sort_order = normalized["sort_order"]
    suite.enabled = normalized["enabled"]
    suite.updated_by = operator.uid if operator else None

    # 替换 members 列表
    suite.members = [
        RecommendedSuiteMember(
            slug=m["slug"],
            name=m["name"],
            description=m["description"],
            sort_order=m["sort_order"],
        )
        for m in normalized["members"]
    ]

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise RecommendedSuiteConflictError(f"slug 已存在或违反唯一约束：{normalized['slug']}") from exc
    await db.refresh(suite, attribute_names=["members"])
    return suite.to_dict()


async def delete_recommended_suite(db: AsyncSession, *, suite_id: int) -> None:
    stmt = _suite_query(db).where(RecommendedSkillSuite.id == suite_id)
    result = await db.execute(stmt)
    suite = result.scalar_one_or_none()
    if suite is None:
        raise RecommendedSuiteNotFoundError(f"找不到套件 id={suite_id}")
    await db.delete(suite)
    await db.commit()


async def set_recommended_suite_enabled(
    db: AsyncSession,
    *,
    suite_id: int,
    enabled: bool,
    operator: User,
) -> dict:
    if not isinstance(enabled, bool):
        raise RecommendedSuiteValidationError("enabled 必须为布尔值")
    stmt = _suite_query(db).where(RecommendedSkillSuite.id == suite_id)
    result = await db.execute(stmt)
    suite = result.scalar_one_or_none()
    if suite is None:
        raise RecommendedSuiteNotFoundError(f"找不到套件 id={suite_id}")
    suite.enabled = enabled
    suite.updated_by = operator.uid if operator else None
    await db.commit()
    await db.refresh(suite, attribute_names=["members"])
    return suite.to_dict()
