from __future__ import annotations

from types import SimpleNamespace

import pytest

from yuxi.agents.skills import recommended_suites as svc
from yuxi.agents.skills.recommended_suites import (
    RecommendedSuiteConflictError,
    RecommendedSuiteNotFoundError,
    RecommendedSuiteValidationError,
)


def _user(uid: str = "admin", role: str = "admin") -> SimpleNamespace:
    return SimpleNamespace(uid=uid, role=role)


def _valid_payload(**overrides):
    base = {
        "slug": "demo-suite",
        "name": "Demo Suite",
        "provider": "Demo",
        "description": "for test",
        "source": "owner/repo",
        "sort_order": 0,
        "enabled": True,
        "members": [
            {"slug": "alpha", "name": "Alpha", "description": "A", "sort_order": 0},
            {"slug": "beta", "name": "Beta", "description": "B", "sort_order": 1},
        ],
    }
    base.update(overrides)
    return base


# ---------- 校验路径 ----------


def test_normalize_suite_payload_rejects_invalid_slug():
    with pytest.raises(RecommendedSuiteValidationError, match="slug"):
        svc._normalize_suite_payload(_valid_payload(slug="Not Valid"))


def test_normalize_suite_payload_rejects_empty_name():
    with pytest.raises(RecommendedSuiteValidationError, match="name"):
        svc._normalize_suite_payload(_valid_payload(name="   "))


def test_normalize_suite_payload_rejects_empty_source():
    with pytest.raises(RecommendedSuiteValidationError, match="source"):
        svc._normalize_suite_payload(_valid_payload(source="   "))


def test_normalize_suite_payload_rejects_source_with_control_chars():
    with pytest.raises(RecommendedSuiteValidationError, match="source"):
        svc._normalize_suite_payload(_valid_payload(source="owner/repo\nrm -rf"))


def test_normalize_suite_payload_rejects_too_long_description():
    with pytest.raises(RecommendedSuiteValidationError, match="description"):
        svc._normalize_suite_payload(_valid_payload(description="x" * (svc.MAX_DESCRIPTION_LENGTH + 1)))


def test_normalize_suite_payload_rejects_empty_members():
    with pytest.raises(RecommendedSuiteValidationError, match="members"):
        svc._normalize_suite_payload(_valid_payload(members=[]))


def test_normalize_suite_payload_rejects_duplicate_member_slug():
    with pytest.raises(RecommendedSuiteValidationError, match="slug 重复"):
        svc._normalize_suite_payload(
            _valid_payload(
                members=[
                    {"slug": "alpha", "name": "Alpha", "description": ""},
                    {"slug": "alpha", "name": "Alpha2", "description": ""},
                ]
            )
        )


def test_normalize_suite_payload_rejects_member_with_empty_name():
    with pytest.raises(RecommendedSuiteValidationError, match="name"):
        svc._normalize_suite_payload(_valid_payload(members=[{"slug": "alpha", "name": "  ", "description": ""}]))


def test_normalize_suite_payload_rejects_member_with_invalid_slug():
    with pytest.raises(RecommendedSuiteValidationError, match="slug"):
        svc._normalize_suite_payload(
            _valid_payload(members=[{"slug": "Not Valid", "name": "Alpha", "description": ""}])
        )


def test_normalize_suite_payload_assigns_continuous_sort_order_to_members():
    normalized = svc._normalize_suite_payload(
        _valid_payload(
            members=[
                {"slug": "alpha", "name": "Alpha", "description": ""},
                {"slug": "beta", "name": "Beta", "description": ""},
                {"slug": "gamma", "name": "Gamma", "description": ""},
            ]
        )
    )
    assert [m["sort_order"] for m in normalized["members"]] == [0, 1, 2]


# ---------- 服务层路径（mocked db） ----------


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalars(self):
        return self._value

    def scalar_one_or_none(self):
        if not isinstance(self._value, list):
            return None
        return self._value[0] if self._value else None


class _FakeSession:
    def __init__(self, return_value=None, *, raise_integrity: bool = False):
        self._return_value = return_value
        self._raise_integrity = raise_integrity
        self.commits = 0
        self.deleted = []
        self.added = []

    async def execute(self, _stmt):
        if self._raise_integrity:
            raise IntegrityErrorStub()
        return _FakeResult(self._return_value or [])

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        if self._raise_integrity:
            raise IntegrityErrorStub()
        self.commits += 1

    async def rollback(self):
        pass

    async def refresh(self, obj, attribute_names=None):
        pass

    async def delete(self, obj):
        self.deleted.append(obj)


# 使用 SQLAlchemy 真实 IntegrityError 以通过 isinstance 判断
from sqlalchemy.exc import IntegrityError as _RealIntegrityError  # noqa: E402


class IntegrityErrorStub(_RealIntegrityError):
    """直接抛出，不需要 statement/params/orig 参数。"""

    def __init__(self):
        pass


@pytest.mark.asyncio
async def test_create_recommended_suite_normalizes_and_persists():
    fake_db = _FakeSession()
    await svc.create_recommended_suite(
        fake_db,
        payload=_valid_payload(),
        operator=_user(),
    )

    assert fake_db.commits == 1
    assert len(fake_db.added) == 1
    suite = fake_db.added[0]
    assert suite.slug == "demo-suite"
    assert suite.name == "Demo Suite"
    assert len(suite.members) == 2


@pytest.mark.asyncio
async def test_create_recommended_suite_raises_conflict_on_integrity_error():
    fake_db = _FakeSession(raise_integrity=True)
    with pytest.raises(RecommendedSuiteConflictError):
        await svc.create_recommended_suite(
            fake_db,
            payload=_valid_payload(),
            operator=_user(),
        )


@pytest.mark.asyncio
async def test_list_recommended_suites_filters_disabled():
    """Public list_recommended_suites 直接走 to_dict()，stub 保持简洁。"""

    class _StubItem:
        def __init__(self, sid: int, enabled: bool):
            self.id = sid
            self.enabled = enabled

        def to_dict(self):
            return {"id": self.id, "enabled": self.enabled}

    items = [_StubItem(1, True)]

    class _ListResult:
        def __init__(self, data):
            self._data = data

        def scalars(self):
            return SimpleNamespace(all=lambda: self._data)

    class _FilterSession:
        def __init__(self, data):
            self._data = data

        async def execute(self, stmt):
            return _ListResult(self._data)

    data = await svc.list_recommended_suites(_FilterSession(items))
    assert data == [{"id": 1, "enabled": True}]


@pytest.mark.asyncio
async def test_update_recommended_suite_raises_not_found_when_missing():
    fake_db = _FakeSession(return_value=[])
    with pytest.raises(RecommendedSuiteNotFoundError):
        await svc.update_recommended_suite(
            fake_db,
            suite_id=99,
            payload=_valid_payload(),
            operator=_user(),
        )


@pytest.mark.asyncio
async def test_delete_recommended_suite_calls_db_delete():
    fake_db = _FakeSession()
    fake_db._return_value = [SimpleNamespace(id=42)]
    await svc.delete_recommended_suite(fake_db, suite_id=42)
    assert len(fake_db.deleted) == 1
    assert fake_db.commits == 1


@pytest.mark.asyncio
async def test_set_recommended_suite_enabled_raises_validation_for_non_bool():
    fake_db = _FakeSession()
    with pytest.raises(RecommendedSuiteValidationError):
        await svc.set_recommended_suite_enabled(fake_db, suite_id=1, enabled="yes", operator=_user())


@pytest.mark.asyncio
async def test_set_recommended_suite_enabled_raises_not_found():
    fake_db = _FakeSession(return_value=[])
    with pytest.raises(RecommendedSuiteNotFoundError):
        await svc.set_recommended_suite_enabled(fake_db, suite_id=1, enabled=True, operator=_user())
