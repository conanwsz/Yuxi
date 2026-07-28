"""Token quota metering, persistence, and callback helpers."""

from __future__ import annotations

import math
from collections.abc import AsyncIterator, Callable, Iterable, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.outputs import ChatGeneration, LLMResult
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi import config as app_config
from yuxi.models.providers.cache import model_cache
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import TokenQuotaLedger, TokenQuotaWeeklyUsage, User
from yuxi.utils.datetime_utils import SHANGHAI_TZ, ensure_utc, utc_now
from yuxi.utils.logging_config import logger

TOKEN_QUOTA_MODE_INHERIT = "inherit"
TOKEN_QUOTA_MODE_CUSTOM = "custom"
TOKEN_QUOTA_MODE_UNLIMITED = "unlimited"
TOKEN_QUOTA_MODES = {
    TOKEN_QUOTA_MODE_INHERIT,
    TOKEN_QUOTA_MODE_CUSTOM,
    TOKEN_QUOTA_MODE_UNLIMITED,
}
_TOKEN_COEFFICIENT_QUANTIZE = Decimal("0.0001")
_BILLING_CONTEXT: ContextVar[BillingContext | None] = ContextVar("yuxi_token_billing_context", default=None)


class TokenQuotaExceededError(RuntimeError):
    """Raised when the caller has exhausted the current weekly token quota."""


@dataclass(frozen=True)
class BillingContext:
    """Request-scoped billing metadata carried into model callbacks."""

    user_id: int | None
    uid_snapshot: str | None = None
    event_id: str | None = None
    source: str = "chat"
    run_id: str | None = None
    request_id: str | None = None
    thread_id: str | None = None
    parent_run_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class UsageSnapshot:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    is_estimated: bool
    estimate_reason: str | None = None


@dataclass
class _PendingUsageEstimate:
    prompt_tokens: int


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _normalize_usage_mapping(value: Mapping[str, Any] | None) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    prompt_tokens = _safe_int(value.get("prompt_tokens", value.get("input_tokens")))
    completion_tokens = _safe_int(value.get("completion_tokens", value.get("output_tokens")))
    total_tokens = _safe_int(value.get("total_tokens"))
    if total_tokens is None and prompt_tokens is not None and completion_tokens is not None:
        total_tokens = prompt_tokens + completion_tokens

    normalized = {
        "prompt_tokens": max(prompt_tokens or 0, 0),
        "completion_tokens": max(completion_tokens or 0, 0),
        "total_tokens": max(total_tokens or 0, 0),
    }
    return normalized if any(normalized.values()) else {}


def _usage_from_generation_message(message: Any) -> dict[str, int]:
    if isinstance(message, AIMessage):
        return _normalize_usage_mapping(getattr(message, "usage_metadata", None))
    return {}


def _usage_from_llm_result(result: LLMResult) -> dict[str, int]:
    for generation_list in reversed(result.generations or []):
        for generation in reversed(generation_list or []):
            if isinstance(generation, ChatGeneration):
                usage = _usage_from_generation_message(generation.message)
                if usage:
                    return usage
            usage = _normalize_usage_mapping(getattr(generation, "generation_info", None))
            if usage:
                return usage
    llm_output = result.llm_output or {}
    for candidate in (llm_output.get("token_usage"), llm_output.get("usage"), llm_output):
        usage = _normalize_usage_mapping(candidate if isinstance(candidate, Mapping) else None)
        if usage:
            return usage
    return {}


def _flatten_messages(batches: Iterable[Iterable[BaseMessage]]) -> list[BaseMessage]:
    return [message for batch in batches for message in batch]


def _count_message_tokens(messages: Iterable[BaseMessage], token_counter: Callable[..., int]) -> int:
    return int(token_counter(list(messages)))


def _count_text_tokens(prompts: Iterable[str]) -> int:
    total_chars = sum(len(prompt) for prompt in prompts if isinstance(prompt, str))
    return max(math.ceil(total_chars / 4), 0)


def _estimate_completion_tokens(result: LLMResult, token_counter: Callable[..., int]) -> int:
    completion_messages: list[BaseMessage] = []
    for generation_list in result.generations or []:
        for generation in generation_list or []:
            if isinstance(generation, ChatGeneration):
                completion_messages.append(generation.message)
    if not completion_messages:
        return 0
    return _count_message_tokens(completion_messages, token_counter)


def _merge_usage(actual: dict[str, int], estimated: dict[str, int]) -> UsageSnapshot:
    if actual:
        prompt_tokens = actual.get("prompt_tokens", estimated.get("prompt_tokens", 0))
        completion_tokens = actual.get("completion_tokens", estimated.get("completion_tokens", 0))
        total_tokens = actual.get("total_tokens") or prompt_tokens + completion_tokens
        return UsageSnapshot(
            prompt_tokens=max(prompt_tokens, 0),
            completion_tokens=max(completion_tokens, 0),
            total_tokens=max(total_tokens, 0),
            is_estimated=False,
        )

    prompt_tokens = max(estimated.get("prompt_tokens", 0), 0)
    completion_tokens = max(estimated.get("completion_tokens", 0), 0)
    total_tokens = estimated.get("total_tokens") or prompt_tokens + completion_tokens
    return UsageSnapshot(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=max(total_tokens, 0),
        is_estimated=True,
        estimate_reason="missing_provider_usage",
    )


def _normalize_token_coefficient(value: Any) -> Decimal:
    try:
        coefficient = Decimal(str(value or 1)).quantize(_TOKEN_COEFFICIENT_QUANTIZE, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("token_coefficient 无法解析") from exc
    if coefficient <= 0:
        raise ValueError("token_coefficient 必须大于 0")
    return coefficient


def _resolve_token_coefficient(model_spec: str) -> Decimal:
    info = model_cache.get_model_info(model_spec)
    if not info:
        return Decimal("1.0000")
    return _normalize_token_coefficient(getattr(info, "token_coefficient", 1.0))


def _weighted_tokens(total_tokens: int, coefficient: Decimal) -> int:
    if total_tokens <= 0:
        return 0
    weighted = (Decimal(total_tokens) * coefficient).to_integral_value(rounding=ROUND_CEILING)
    return int(weighted)


def _week_start_for(value: datetime) -> date:
    local_value = ensure_utc(value).astimezone(SHANGHAI_TZ)
    start = local_value.date() - timedelta(days=local_value.weekday())
    return start


async def _get_user(db: AsyncSession, user_or_id: User | int | None) -> User | None:
    if isinstance(user_or_id, User):
        return user_or_id
    if user_or_id is None:
        return None
    return await db.get(User, user_or_id)


def _resolve_quota_mode_and_limit(user: User | None) -> tuple[str, int | None]:
    if user is None:
        return TOKEN_QUOTA_MODE_INHERIT, int(app_config.default_weekly_token_quota)

    mode = str(user.token_quota_mode or TOKEN_QUOTA_MODE_INHERIT).strip()
    if mode not in TOKEN_QUOTA_MODES:
        mode = TOKEN_QUOTA_MODE_INHERIT

    if mode == TOKEN_QUOTA_MODE_UNLIMITED:
        return mode, None
    if mode == TOKEN_QUOTA_MODE_CUSTOM:
        quota = _safe_int(user.weekly_token_quota)
        if quota is None or quota <= 0:
            raise ValueError(f"用户 {user.uid} 的自定义 weekly_token_quota 无效")
        return mode, quota
    return TOKEN_QUOTA_MODE_INHERIT, int(app_config.default_weekly_token_quota)


async def _get_or_create_weekly_usage(
    db: AsyncSession,
    *,
    user: User,
    uid_snapshot: str,
    week_start: date,
    quota_mode: str,
    quota_limit: int | None,
) -> TokenQuotaWeeklyUsage:
    result = await db.execute(
        select(TokenQuotaWeeklyUsage).where(
            TokenQuotaWeeklyUsage.user_id == user.id,
            TokenQuotaWeeklyUsage.week_start == week_start,
        )
    )
    weekly = result.scalar_one_or_none()
    if weekly is not None:
        return weekly

    weekly = TokenQuotaWeeklyUsage(
        user_id=user.id,
        uid_snapshot=uid_snapshot,
        week_start=week_start,
        quota_mode=quota_mode,
        quota_limit=quota_limit,
    )
    try:
        async with db.begin_nested():
            db.add(weekly)
            await db.flush()
    except IntegrityError:
        result = await db.execute(
            select(TokenQuotaWeeklyUsage).where(
                TokenQuotaWeeklyUsage.user_id == user.id,
                TokenQuotaWeeklyUsage.week_start == week_start,
            )
        )
        weekly = result.scalar_one()
    return weekly


async def status(
    db: AsyncSession,
    user_or_id: User | int,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    user = await _get_user(db, user_or_id)
    if user is None:
        raise ValueError("用户不存在，无法查询 token 配额")

    week_start = _week_start_for(now or utc_now())
    quota_mode, effective_weekly_token_quota = _resolve_quota_mode_and_limit(user)
    result = await db.execute(
        select(TokenQuotaWeeklyUsage).where(
            TokenQuotaWeeklyUsage.user_id == user.id,
            TokenQuotaWeeklyUsage.week_start == week_start,
        )
    )
    weekly = result.scalar_one_or_none()

    prompt_tokens = weekly.prompt_tokens if weekly else 0
    completion_tokens = weekly.completion_tokens if weekly else 0
    total_tokens = weekly.total_tokens if weekly else 0
    weighted_tokens = weekly.weighted_tokens if weekly else 0
    remaining_weighted_tokens = (
        None if effective_weekly_token_quota is None else max(effective_weekly_token_quota - weighted_tokens, 0)
    )

    return {
        "user_id": user.id,
        "uid": user.uid,
        "mode": quota_mode,
        "token_quota_mode": quota_mode,
        "configured_weekly_token_quota": user.weekly_token_quota,
        "effective_weekly_token_quota": effective_weekly_token_quota,
        "effective_quota": effective_weekly_token_quota,
        "is_unlimited": effective_weekly_token_quota is None,
        "week_start": week_start.isoformat(),
        "week_end": (week_start + timedelta(days=6)).isoformat(),
        "reset_at": _format_reset_at(week_start),
        "week_label": _format_week_label(
            week_start.isoformat(),
            (week_start + timedelta(days=6)).isoformat(),
        ),
        "tracking_since": weekly.created_at.isoformat() if weekly else None,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "weighted_tokens": weighted_tokens,
        "used": weighted_tokens,
        "used_tokens": weighted_tokens,
        "remaining": remaining_weighted_tokens,
        "remaining_tokens": remaining_weighted_tokens,
        "usage_ratio": (
            None if not effective_weekly_token_quota else round(weighted_tokens / effective_weekly_token_quota, 6)
        ),
        "event_count": weekly.event_count if weekly else 0,
        "estimated_event_count": weekly.estimated_event_count if weekly else 0,
        "by_model": [],
    }


def _format_reset_at(week_start: date) -> str:
    """按 Asia/Shanghai 零点生成可读的下次重置时间。"""
    next_monday = week_start + timedelta(days=7)
    return f"{next_monday.isoformat()} 00:00"


# ---- public aliases used by routers (test-friendly naming) ----
get_user_token_quota_status = status


async def get_user_token_quota_payload(
    db: AsyncSession,
    user: User,
    *,
    now: datetime | None = None,
    model_breakdown: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Aggregate the per-user quota status payload returned in user API responses."""
    quota_status = await get_user_token_quota_status(db, user, now=now)
    quota_status["by_model"] = model_breakdown or []
    return {
        "token_quota_mode": quota_status["token_quota_mode"],
        "weekly_token_quota": quota_status["configured_weekly_token_quota"],
        "token_quota": quota_status,
    }


def _format_week_label(week_start: str, week_end: str) -> str:
    """前端展示用的本周额度区间文本。"""
    try:
        start = date.fromisoformat(week_start)
        end = date.fromisoformat(week_end)
    except (TypeError, ValueError):
        return "本周额度"
    return f"{start.isoformat()} ~ {end.isoformat()}"


async def _collect_model_breakdown(
    db: AsyncSession,
    user: User,
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """按模型汇总用户本周加权 token 消耗。"""
    week_start = _week_start_for(now or utc_now())
    result = await db.execute(
        select(TokenQuotaLedger).where(
            TokenQuotaLedger.user_id == user.id,
            TokenQuotaLedger.week_start == week_start,
        )
    )
    aggregate: dict[str, dict[str, Any]] = {}
    for entry in result.scalars():
        bucket = aggregate.setdefault(
            entry.model_spec,
            {
                "model": entry.model_spec,
                "spec": entry.model_spec,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "weighted_tokens": 0,
                "event_count": 0,
                "estimated_event_count": 0,
            },
        )
        bucket["prompt_tokens"] += entry.prompt_tokens
        bucket["completion_tokens"] += entry.completion_tokens
        bucket["total_tokens"] += entry.total_tokens
        bucket["weighted_tokens"] += entry.weighted_tokens
        bucket["event_count"] += 1
        if entry.is_estimated:
            bucket["estimated_event_count"] += 1
    breakdown: list[dict[str, Any]] = []
    for spec, bucket in aggregate.items():
        bucket["used"] = bucket["weighted_tokens"]
        bucket["effective_used"] = bucket["weighted_tokens"]
        bucket["display_name"] = spec
        breakdown.append(bucket)
    breakdown.sort(key=lambda item: item["weighted_tokens"], reverse=True)
    return breakdown


async def get_user_token_quota_payload_with_breakdown(
    db: AsyncSession,
    user: User,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """User self quota API: status + per-model breakdown."""
    breakdown = await _collect_model_breakdown(db, user, now=now)
    return await get_user_token_quota_payload(db, user, now=now, model_breakdown=breakdown)


async def batch_get_user_token_quota_statuses(
    db: AsyncSession,
    users: list[User],
    *,
    now: datetime | None = None,
) -> dict[int, dict[str, Any]]:
    """Batch aggregate quota status for a list of users to avoid N+1 lookups."""
    if not users:
        return {}
    week_start = _week_start_for(now or utc_now())
    user_ids = [user.id for user in users if user is not None]
    weekly_rows: dict[int, TokenQuotaWeeklyUsage] = {}
    ledger_aggregate: dict[int, dict[str, dict[str, Any]]] = {}
    if user_ids:
        result = await db.execute(
            select(TokenQuotaWeeklyUsage).where(
                TokenQuotaWeeklyUsage.user_id.in_(user_ids),
                TokenQuotaWeeklyUsage.week_start == week_start,
            )
        )
        for row in result.scalars():
            weekly_rows[row.user_id] = row

        ledger_result = await db.execute(
            select(TokenQuotaLedger).where(
                TokenQuotaLedger.user_id.in_(user_ids),
                TokenQuotaLedger.week_start == week_start,
            )
        )
        for entry in ledger_result.scalars():
            bucket_by_user = ledger_aggregate.setdefault(entry.user_id, {})
            bucket = bucket_by_user.setdefault(
                entry.model_spec,
                {
                    "model": entry.model_spec,
                    "spec": entry.model_spec,
                    "display_name": entry.model_spec,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "weighted_tokens": 0,
                    "event_count": 0,
                    "estimated_event_count": 0,
                },
            )
            bucket["prompt_tokens"] += entry.prompt_tokens
            bucket["completion_tokens"] += entry.completion_tokens
            bucket["total_tokens"] += entry.total_tokens
            bucket["weighted_tokens"] += entry.weighted_tokens
            bucket["event_count"] += 1
            if entry.is_estimated:
                bucket["estimated_event_count"] += 1

    statuses: dict[int, dict[str, Any]] = {}
    for user in users:
        if user is None:
            continue
        quota_mode, effective_weekly_token_quota = _resolve_quota_mode_and_limit(user)
        weekly = weekly_rows.get(user.id)
        prompt_tokens = weekly.prompt_tokens if weekly else 0
        completion_tokens = weekly.completion_tokens if weekly else 0
        total_tokens = weekly.total_tokens if weekly else 0
        weighted_tokens = weekly.weighted_tokens if weekly else 0
        remaining_weighted_tokens = (
            None if effective_weekly_token_quota is None else max(effective_weekly_token_quota - weighted_tokens, 0)
        )
        per_model_breakdown = []
        for spec, bucket in ledger_aggregate.get(user.id, {}).items():
            bucket = dict(bucket)
            bucket["used"] = bucket["weighted_tokens"]
            bucket["effective_used"] = bucket["weighted_tokens"]
            per_model_breakdown.append(bucket)
        per_model_breakdown.sort(key=lambda item: item["weighted_tokens"], reverse=True)

        statuses[user.id] = {
            "user_id": user.id,
            "uid": user.uid,
            "mode": quota_mode,
            "token_quota_mode": quota_mode,
            "configured_weekly_token_quota": user.weekly_token_quota,
            "effective_weekly_token_quota": effective_weekly_token_quota,
            "effective_quota": effective_weekly_token_quota,
            "is_unlimited": effective_weekly_token_quota is None,
            "week_start": week_start.isoformat(),
            "week_end": (week_start + timedelta(days=6)).isoformat(),
            "reset_at": _format_reset_at(week_start),
            "tracking_since": weekly.created_at.isoformat() if weekly else None,
            "week_label": _format_week_label(week_start.isoformat(), (week_start + timedelta(days=6)).isoformat()),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "weighted_tokens": weighted_tokens,
            "used": weighted_tokens,
            "used_tokens": weighted_tokens,
            "remaining": remaining_weighted_tokens,
            "remaining_tokens": remaining_weighted_tokens,
            "usage_ratio": (
                None if not effective_weekly_token_quota else round(weighted_tokens / effective_weekly_token_quota, 6)
            ),
            "event_count": weekly.event_count if weekly else 0,
            "estimated_event_count": weekly.estimated_event_count if weekly else 0,
            "by_model": per_model_breakdown,
        }
    return statuses


async def assert_quota_available(
    db: AsyncSession,
    user_or_id: User | int,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    quota_status = await status(db, user_or_id, now=now)
    remaining = quota_status.get("remaining")
    if remaining is not None and remaining <= 0:
        raise TokenQuotaExceededError(f"用户 {quota_status['uid']} 本周 token 额度已用尽")
    return quota_status


async def settle(
    db: AsyncSession,
    *,
    model_spec: str,
    event_id: str,
    user_id: int | None = None,
    uid_snapshot: str | None = None,
    usage: Mapping[str, Any] | None = None,
    estimated_usage: Mapping[str, Any] | None = None,
    occurred_at: datetime | None = None,
    source: str = "chat",
    run_id: str | None = None,
    request_id: str | None = None,
    thread_id: str | None = None,
    parent_run_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not model_spec:
        raise ValueError("model_spec 不能为空")
    if not event_id:
        raise ValueError("event_id 不能为空")

    user = await _get_user(db, user_id)
    resolved_uid_snapshot = uid_snapshot or getattr(user, "uid", None)
    if not resolved_uid_snapshot:
        raise ValueError("settle 需要 uid_snapshot 或有效的 user_id")

    quota_mode, quota_limit = _resolve_quota_mode_and_limit(user)
    actual_usage = _normalize_usage_mapping(usage)
    estimate_usage = _normalize_usage_mapping(estimated_usage)
    usage_snapshot = _merge_usage(actual_usage, estimate_usage)
    coefficient = _resolve_token_coefficient(model_spec)
    weighted_tokens = _weighted_tokens(usage_snapshot.total_tokens, coefficient)
    created_at = ensure_utc(occurred_at or utc_now()).replace(tzinfo=None)
    week_start = _week_start_for(occurred_at or utc_now())

    ledger = TokenQuotaLedger(
        event_id=event_id,
        user_id=user.id if user else None,
        uid_snapshot=resolved_uid_snapshot,
        week_start=week_start,
        model_spec=model_spec,
        prompt_tokens=usage_snapshot.prompt_tokens,
        completion_tokens=usage_snapshot.completion_tokens,
        total_tokens=usage_snapshot.total_tokens,
        weighted_tokens=weighted_tokens,
        token_coefficient=coefficient,
        is_estimated=usage_snapshot.is_estimated,
        estimate_reason=usage_snapshot.estimate_reason,
        source=source,
        run_id=run_id,
        request_id=request_id,
        thread_id=thread_id,
        parent_run_id=parent_run_id,
        metadata_json=dict(metadata or {}),
        created_at=created_at,
    )

    inserted = False
    try:
        async with db.begin_nested():
            db.add(ledger)
            await db.flush()
        inserted = True
    except IntegrityError:
        result = await db.execute(select(TokenQuotaLedger).where(TokenQuotaLedger.event_id == event_id))
        existing = result.scalar_one()
        logger.debug(f"Token quota settlement skipped duplicate event: {event_id}")
        return {
            "applied": False,
            "duplicate": True,
            "ledger": existing.to_dict(),
            "status": await status(db, user) if user else None,
        }

    weekly = None
    if inserted and user is not None:
        weekly = await _get_or_create_weekly_usage(
            db,
            user=user,
            uid_snapshot=resolved_uid_snapshot,
            week_start=week_start,
            quota_mode=quota_mode,
            quota_limit=quota_limit,
        )
        weekly.uid_snapshot = resolved_uid_snapshot
        weekly.quota_mode = quota_mode
        weekly.quota_limit = quota_limit
        weekly.prompt_tokens += usage_snapshot.prompt_tokens
        weekly.completion_tokens += usage_snapshot.completion_tokens
        weekly.total_tokens += usage_snapshot.total_tokens
        weekly.weighted_tokens += weighted_tokens
        weekly.event_count += 1
        if usage_snapshot.is_estimated:
            weekly.estimated_event_count += 1
        await db.flush()

    return {
        "applied": True,
        "duplicate": False,
        "ledger": ledger.to_dict(),
        "weekly_usage": weekly.to_dict() if weekly is not None else None,
        "status": await status(db, user) if user else None,
    }


def get_token_billing_context() -> BillingContext | None:
    return _BILLING_CONTEXT.get()


@contextmanager
def token_billing_context(
    *,
    user_id: int | None,
    uid_snapshot: str | None = None,
    event_id: str | None = None,
    source: str = "chat",
    run_id: str | None = None,
    request_id: str | None = None,
    thread_id: str | None = None,
    parent_run_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
):
    token = _BILLING_CONTEXT.set(
        BillingContext(
            user_id=user_id,
            uid_snapshot=uid_snapshot,
            event_id=event_id,
            source=source,
            run_id=run_id,
            request_id=request_id,
            thread_id=thread_id,
            parent_run_id=parent_run_id,
            metadata=dict(metadata or {}),
        )
    )
    try:
        yield
    finally:
        _BILLING_CONTEXT.reset(token)


class TokenQuotaCallback(AsyncCallbackHandler):
    """Callback that settles token usage after each chat model invocation."""

    raise_error = True

    def __init__(
        self,
        model_spec: str,
        *,
        session_factory: Callable[[], AsyncIterator[AsyncSession]] | None = None,
        token_counter: Callable[..., int] = count_tokens_approximately,
    ) -> None:
        super().__init__()
        self.model_spec = model_spec
        self._session_factory = session_factory or pg_manager.get_async_session_context
        self._token_counter = token_counter
        self._pending: dict[UUID, _PendingUsageEstimate] = {}

    async def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[BaseMessage]],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        del serialized, parent_run_id, tags, metadata, kwargs
        flattened = _flatten_messages(messages)
        self._pending[run_id] = _PendingUsageEstimate(
            prompt_tokens=_count_message_tokens(flattened, self._token_counter)
        )
        return None

    async def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        del serialized, parent_run_id, tags, metadata, kwargs
        self._pending.setdefault(run_id, _PendingUsageEstimate(prompt_tokens=_count_text_tokens(prompts)))

    async def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        del parent_run_id, tags, kwargs
        context = get_token_billing_context()
        pending = self._pending.pop(run_id, None)
        if context is None or (context.user_id is None and not context.uid_snapshot):
            return

        estimated_usage = {
            "prompt_tokens": pending.prompt_tokens if pending else 0,
            "completion_tokens": _estimate_completion_tokens(response, self._token_counter),
        }
        estimated_usage["total_tokens"] = estimated_usage["prompt_tokens"] + estimated_usage["completion_tokens"]
        actual_usage = _usage_from_llm_result(response)
        event_id = context.event_id or f"{context.source}:{run_id}"

        async with self._session_factory() as db:
            await settle(
                db,
                model_spec=self.model_spec,
                event_id=event_id,
                user_id=context.user_id,
                uid_snapshot=context.uid_snapshot,
                usage=actual_usage,
                estimated_usage=estimated_usage,
                source=context.source,
                run_id=context.run_id,
                request_id=context.request_id,
                thread_id=context.thread_id,
                parent_run_id=context.parent_run_id,
                metadata=context.metadata,
            )

    async def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        del error, parent_run_id, tags, kwargs
        self._pending.pop(run_id, None)


def create_token_quota_callback(
    model_spec: str,
    *,
    session_factory: Callable[[], AsyncIterator[AsyncSession]] | None = None,
    token_counter: Callable[..., int] = count_tokens_approximately,
) -> TokenQuotaCallback:
    return TokenQuotaCallback(model_spec, session_factory=session_factory, token_counter=token_counter)


__all__ = [
    "BillingContext",
    "TokenQuotaCallback",
    "TokenQuotaExceededError",
    "assert_quota_available",
    "batch_get_user_token_quota_statuses",
    "create_token_quota_callback",
    "get_token_billing_context",
    "get_user_token_quota_payload",
    "get_user_token_quota_status",
    "settle",
    "status",
    "token_billing_context",
]
