from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, datetime, UTC
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from yuxi import config as app_config
from yuxi.services import token_quota_service as quota_service
from yuxi.services.token_quota_service import (
    BillingContext,
    TokenQuotaCallback,
    TokenQuotaExceededError,
    assert_quota_available,
    batch_get_user_token_quota_statuses,
    get_user_token_quota_payload_with_breakdown,
    settle,
    status,
    token_billing_context,
)
from yuxi.storage.postgres.models_business import (
    Base,
    Department,
    DepartmentClosure,
    User,
    UserDepartmentMembership,
)
from yuxi.utils.datetime_utils import SHANGHAI_TZ

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


@pytest.fixture()
async def quota_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        department = Department(name="默认", status="active", is_system=False)
        db.add(department)
        await db.flush()
        db.add(DepartmentClosure(ancestor_id=department.id, descendant_id=department.id, depth=0))
        await db.commit()
    try:
        yield factory
    finally:
        await engine.dispose()


async def _build_user(
    db: AsyncSession,
    *,
    department_id: int,
    uid: str,
    username: str,
    token_quota_mode: str = "inherit",
    weekly_token_quota: int | None = None,
) -> User:
    user = User(
        username=username,
        uid=uid,
        password_hash="$argon2id$placeholder",
        role="user",
        department_id=department_id,
        token_quota_mode=token_quota_mode,
        weekly_token_quota=weekly_token_quota,
    )
    db.add(user)
    await db.flush()
    db.add(
        UserDepartmentMembership(
            user_id=user.id,
            department_id=department_id,
            membership_type="primary",
            status="active",
        )
    )
    await db.commit()
    await db.refresh(user)
    return user


async def test_week_start_for_monday_in_shanghai_timezone():
    week_start = quota_service._week_start_for(datetime(2026, 7, 22, 12, 0, tzinfo=SHANGHAI_TZ))
    assert week_start == date(2026, 7, 20)


async def test_week_start_for_sunday_in_shanghai_rolls_back_to_monday():
    week_start = quota_service._week_start_for(datetime(2026, 7, 26, 23, 0, tzinfo=SHANGHAI_TZ))
    assert week_start == date(2026, 7, 20)


async def test_week_start_for_uses_utc_when_aware_input_uses_utc():
    week_start = quota_service._week_start_for(datetime(2026, 7, 21, 1, 0, tzinfo=UTC))
    # 2026-07-21 01:00 UTC = 2026-07-21 09:00 Shanghai, still Monday
    assert week_start == date(2026, 7, 20)


async def test_resolve_quota_mode_inherit_returns_default(quota_session):
    async with quota_session() as db:
        user = await _build_user(
            db,
            department_id=1,
            uid="alice",
            username="alice",
            token_quota_mode="inherit",
        )
        mode, limit = quota_service._resolve_quota_mode_and_limit(user)
        assert mode == "inherit"
        assert limit == int(app_config.default_weekly_token_quota)


async def test_resolve_quota_mode_custom_uses_configured_value(quota_session):
    async with quota_session() as db:
        user = await _build_user(
            db,
            department_id=1,
            uid="bob",
            username="bob",
            token_quota_mode="custom",
            weekly_token_quota=12345,
        )
        mode, limit = quota_service._resolve_quota_mode_and_limit(user)
        assert mode == "custom"
        assert limit == 12345


async def test_resolve_quota_mode_unlimited_returns_none(quota_session):
    async with quota_session() as db:
        user = await _build_user(
            db,
            department_id=1,
            uid="carol",
            username="carol",
            token_quota_mode="unlimited",
        )
        mode, limit = quota_service._resolve_quota_mode_and_limit(user)
        assert mode == "unlimited"
        assert limit is None


async def test_status_for_inherit_user_returns_effective_default(quota_session):
    async with quota_session() as db:
        user = await _build_user(
            db,
            department_id=1,
            uid="dan",
            username="dan",
            token_quota_mode="inherit",
        )
        quota = await status(db, user)
        assert quota["mode"] == "inherit"
        assert quota["effective_weekly_token_quota"] == int(app_config.default_weekly_token_quota)
        assert quota["effective_quota"] == quota["effective_weekly_token_quota"]
        assert quota["weighted_tokens"] == 0
        assert quota["is_unlimited"] is False
        assert quota["reset_at"] == "2026-07-27 00:00"
        assert quota["week_label"]


async def test_status_after_settle_aggregates_token_usage(quota_session, monkeypatch):
    from yuxi.models.providers import cache as cache_module

    class _StubInfo:
        token_coefficient = 2.0

    monkeypatch.setattr(
        cache_module.model_cache,
        "get_model_info",
        lambda spec: _StubInfo() if spec == "provider:gpt-4" else None,
    )

    async with quota_session() as db:
        user = await _build_user(
            db,
            department_id=1,
            uid="erin",
            username="erin",
            token_quota_mode="custom",
            weekly_token_quota=10000,
        )
        # First settle - real usage, coefficient 2.0
        result = await settle(
            db,
            model_spec="provider:gpt-4",
            event_id="evt-1",
            user_id=user.id,
            uid_snapshot=user.uid,
            usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        )
        assert result["applied"] is True
        assert result["duplicate"] is False

        # Second settle - estimated usage (no provider info, default 1.0)
        await settle(
            db,
            model_spec="provider:claude",
            event_id="evt-2",
            user_id=user.id,
            uid_snapshot=user.uid,
            usage={},
            estimated_usage={"prompt_tokens": 80, "completion_tokens": 40, "total_tokens": 120},
            source="summary",
        )
        await db.commit()

        quota = await status(db, user)
        # 150 tokens * 2.0 = 300; 120 * 1.0 = 120; total 420
        assert quota["weighted_tokens"] == 420
        assert quota["event_count"] == 2
        assert quota["estimated_event_count"] == 1
        assert quota["remaining_tokens"] == 10000 - 420


async def test_get_user_token_quota_payload_preserves_remaining(quota_session):
    from yuxi.services.token_quota_service import get_user_token_quota_payload

    async with quota_session() as db:
        user = await _build_user(
            db,
            department_id=1,
            uid="grace",
            username="grace",
            token_quota_mode="custom",
            weekly_token_quota=5000,
        )
        await settle(
            db,
            model_spec="provider:gpt-4",
            event_id="evt-grace",
            user_id=user.id,
            uid_snapshot=user.uid,
            usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        )
        await db.commit()

        payload = await get_user_token_quota_payload(db, user)
        token_quota = payload["token_quota"]
        assert token_quota["effective_quota"] == 5000
        assert token_quota["used"] == token_quota["weighted_tokens"]
        assert token_quota["remaining"] == 5000 - token_quota["weighted_tokens"]


async def test_settle_with_duplicate_event_id_does_not_double_count(quota_session):
    async with quota_session() as db:
        user = await _build_user(
            db,
            department_id=1,
            uid="frank",
            username="frank",
            token_quota_mode="custom",
            weekly_token_quota=10000,
        )
        first = await settle(
            db,
            model_spec="provider:gpt-4",
            event_id="evt-dup",
            user_id=user.id,
            uid_snapshot=user.uid,
            usage={"prompt_tokens": 100, "completion_tokens": 100, "total_tokens": 200},
        )
        await db.commit()
        assert first["applied"] is True

        second = await settle(
            db,
            model_spec="provider:gpt-4",
            event_id="evt-dup",
            user_id=user.id,
            uid_snapshot=user.uid,
            usage={"prompt_tokens": 999, "completion_tokens": 999, "total_tokens": 1998},
        )
        await db.commit()
        assert second["applied"] is False
        assert second["duplicate"] is True

        quota = await status(db, user)
        # Only first event's 200 tokens counted
        assert quota["weighted_tokens"] == 200


async def test_assert_quota_available_raises_when_exhausted(quota_session, monkeypatch):
    from yuxi.models.providers import cache as cache_module

    monkeypatch.setattr(cache_module.model_cache, "get_model_info", lambda spec: None)

    async with quota_session() as db:
        user = await _build_user(
            db,
            department_id=1,
            uid="grace",
            username="grace",
            token_quota_mode="custom",
            weekly_token_quota=10,
        )
        await settle(
            db,
            model_spec="provider:gpt-4",
            event_id="evt-x",
            user_id=user.id,
            uid_snapshot=user.uid,
            usage={"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
        )
        await db.commit()

        with pytest.raises(TokenQuotaExceededError):
            await assert_quota_available(db, user)


async def test_assert_quota_available_passes_for_unlimited_user(quota_session, monkeypatch):
    from yuxi.models.providers import cache as cache_module

    monkeypatch.setattr(cache_module.model_cache, "get_model_info", lambda spec: None)

    async with quota_session() as db:
        user = await _build_user(
            db,
            department_id=1,
            uid="heidi",
            username="heidi",
            token_quota_mode="unlimited",
        )
        # Even after huge usage, unlimited users should pass
        await settle(
            db,
            model_spec="provider:gpt-4",
            event_id="evt-uni",
            user_id=user.id,
            uid_snapshot=user.uid,
            usage={"prompt_tokens": 999999, "completion_tokens": 999999, "total_tokens": 1999998},
        )
        await db.commit()
        quota = await assert_quota_available(db, user)
        assert quota["is_unlimited"] is True


async def test_batch_get_user_token_quota_statuses_avoids_n_plus_1(quota_session, monkeypatch):
    from yuxi.models.providers import cache as cache_module

    monkeypatch.setattr(
        cache_module.model_cache,
        "get_model_info",
        lambda spec: None,
    )

    async with quota_session() as db:
        user_a = await _build_user(
            db, department_id=1, uid="u-a", username="u-a", token_quota_mode="custom", weekly_token_quota=1000
        )
        user_b = await _build_user(
            db, department_id=1, uid="u-b", username="u-b", token_quota_mode="custom", weekly_token_quota=2000
        )
        await settle(
            db,
            model_spec="provider:gpt-4",
            event_id="evt-a",
            user_id=user_a.id,
            uid_snapshot=user_a.uid,
            usage={"prompt_tokens": 50, "completion_tokens": 50, "total_tokens": 100},
        )
        await db.commit()

        statuses = await batch_get_user_token_quota_statuses(db, [user_a, user_b])
        assert set(statuses) == {user_a.id, user_b.id}
        assert statuses[user_a.id]["weighted_tokens"] == 100
        assert statuses[user_a.id]["effective_weekly_token_quota"] == 1000
        assert statuses[user_b.id]["weighted_tokens"] == 0
        assert statuses[user_b.id]["effective_weekly_token_quota"] == 2000


async def test_get_user_token_quota_payload_with_breakdown_includes_models(quota_session, monkeypatch):
    from yuxi.models.providers import cache as cache_module

    class _StubInfo:
        def __init__(self, coef):
            self.token_coefficient = coef

    def fake_get_info(spec):
        return {"provider:gpt-4": _StubInfo(2.0)}.get(spec)

    monkeypatch.setattr(cache_module.model_cache, "get_model_info", fake_get_info)

    async with quota_session() as db:
        user = await _build_user(
            db,
            department_id=1,
            uid="ivan",
            username="ivan",
            token_quota_mode="custom",
            weekly_token_quota=5000,
        )
        await settle(
            db,
            model_spec="provider:gpt-4",
            event_id="evt-gpt",
            user_id=user.id,
            uid_snapshot=user.uid,
            usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        )
        await settle(
            db,
            model_spec="provider:claude",
            event_id="evt-claude",
            user_id=user.id,
            uid_snapshot=user.uid,
            usage={"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
        )
        await db.commit()

        payload = await get_user_token_quota_payload_with_breakdown(db, user)
        assert payload["token_quota_mode"] == "custom"
        assert payload["token_quota"]["by_model"]
        models = {row["model"] for row in payload["token_quota"]["by_model"]}
        assert models == {"provider:gpt-4", "provider:claude"}
        gpt = next(row for row in payload["token_quota"]["by_model"] if row["model"] == "provider:gpt-4")
        assert gpt["weighted_tokens"] == 60
        claude = next(row for row in payload["token_quota"]["by_model"] if row["model"] == "provider:claude")
        assert claude["weighted_tokens"] == 10


async def test_normalize_token_coefficient_rejects_unparseable():
    # The service treats falsy values as the default 1.0; only unparseable strings
    # trigger an explicit ValueError.
    with pytest.raises(ValueError):
        quota_service._normalize_token_coefficient("not a number")
    with pytest.raises(ValueError):
        quota_service._normalize_token_coefficient(object())


async def test_weighted_tokens_uses_ceiling():
    weighted = quota_service._weighted_tokens(7, Decimal("1.5"))
    assert weighted == 11  # 7 * 1.5 = 10.5 -> ceil = 11


async def test_token_billing_context_propagates_into_callback():
    captured: dict[str, BillingContext | None] = {"value": None}

    with token_billing_context(
        user_id=42,
        uid_snapshot="alpha",
        source="agent_call",
        run_id="run-1",
        request_id="req-1",
        thread_id="thr-1",
    ):
        captured["value"] = quota_service.get_token_billing_context()

    assert isinstance(captured["value"], BillingContext)
    assert captured["value"].user_id == 42
    assert captured["value"].source == "agent_call"
    assert captured["value"].run_id == "run-1"
    assert quota_service.get_token_billing_context() is None


async def test_callback_skips_settlement_without_billing_context():
    """Without a billing context, on_llm_end should be a no-op (no DB session opened)."""
    opened: list[bool] = []

    @asynccontextmanager
    async def fake_session_ctx():
        opened.append(True)
        yield SimpleNamespace()

    callback = TokenQuotaCallback(
        model_spec="provider:gpt-4",
        session_factory=fake_session_ctx,
    )
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, LLMResult

    response = LLMResult(generations=[[ChatGeneration(message=AIMessage(content="hi"))]])
    await callback.on_llm_end(response, run_id=__import__("uuid").uuid4())
    assert opened == []  # no DB session opened because no billing context
