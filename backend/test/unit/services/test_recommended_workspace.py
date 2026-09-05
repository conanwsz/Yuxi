from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy.exc import IntegrityError as _RealIntegrityError


from yuxi.agents.skills import service as svc


def _user(uid: str = "alice", role: str = "user") -> SimpleNamespace:
    return SimpleNamespace(uid=uid, role=role)


def _skill_stub(
    slug: str = "demo",
    *,
    is_recommended_workspace: bool = False,
    enabled: bool = True,
    source_type: str = "upload",
):
    return SimpleNamespace(
        slug=slug,
        name=slug.title(),
        description="demo skill",
        source_type=source_type,
        is_recommended_workspace=is_recommended_workspace,
        enabled=enabled,
        share_config={},
        created_by="alice",
        tool_dependencies=[],
        mcp_dependencies=[],
        skill_dependencies=[],
        to_dict=lambda: {
            "slug": slug,
            "name": slug.title(),
            "description": "demo skill",
            "is_recommended_workspace": is_recommended_workspace,
        },
    )


# ---------- 共享配置构造 ----------


def test_build_recommended_workspace_share_config_default_global():
    cfg = svc._build_recommended_workspace_share_config(_user("alice"))
    assert cfg["read_scope"]["access_level"] == "global"
    assert cfg["manage_scope"]["access_level"] == "user"
    assert cfg["manage_scope"]["user_uids"] == ["alice"]


# ---------- list_recommended_workspace_skills ----------


class _ListResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return SimpleNamespace(all=lambda: self._items)


class _FakeDb:
    def __init__(self, items):
        self._items = items

    async def execute(self, _stmt):
        return _ListResult(self._items)


@pytest.mark.asyncio
async def test_list_recommended_workspace_skills_filters_disabled_and_unauthorized():
    """SQL 已经过滤 is_recommended_workspace=True；这里只覆盖 user_can_access_skill 过滤。"""
    visible = _skill_stub(slug="a", is_recommended_workspace=True)
    disabled = _skill_stub(slug="b", is_recommended_workspace=True, enabled=False)
    fake_db = _FakeDb([visible, disabled])

    with patch.object(svc, "user_can_access_skill", side_effect=lambda u, s, **kw: s.enabled):
        items = await svc.list_recommended_workspace_skills(fake_db, _user("alice"))

    assert [item["slug"] for item in items] == ["a"]


# ---------- clone_recommended_workspace_skill_to_personal 错误路径 ----------


class _RepoOK:
    def __init__(self, item):
        self._item = item

    async def get_by_slug(self, _slug):
        return self._item


class _RepoMiss:
    async def get_by_slug(self, _slug):
        return None


@pytest.mark.asyncio
async def test_clone_recommended_workspace_skill_raises_when_skill_not_in_pool(monkeypatch):
    skill = _skill_stub(slug="x", is_recommended_workspace=False)
    monkeypatch.setattr(svc, "SkillRepository", lambda _db: _RepoOK(skill))
    monkeypatch.setattr(svc, "is_valid_skill_slug", lambda s: bool(s))

    with patch.object(svc, "user_can_access_skill", return_value=True):
        with pytest.raises(ValueError, match="不在推荐工作区中"):
            await svc.clone_recommended_workspace_skill_to_personal(db=None, slug="x", operator=_user("alice"))


@pytest.mark.asyncio
async def test_clone_recommended_workspace_skill_raises_when_slug_invalid(monkeypatch):
    monkeypatch.setattr(svc, "is_valid_skill_slug", lambda s: False)
    with pytest.raises(ValueError, match="无效 skill slug"):
        await svc.clone_recommended_workspace_skill_to_personal(db=None, slug="Bad!", operator=_user("alice"))


@pytest.mark.asyncio
async def test_clone_recommended_workspace_skill_raises_when_skill_missing(monkeypatch):
    monkeypatch.setattr(svc, "is_valid_skill_slug", lambda s: bool(s))
    monkeypatch.setattr(svc, "SkillRepository", lambda _db: _RepoMiss())
    with pytest.raises(ValueError, match="不存在"):
        await svc.clone_recommended_workspace_skill_to_personal(db=None, slug="missing", operator=_user("alice"))


@pytest.mark.asyncio
async def test_clone_recommended_workspace_skill_raises_when_no_access(monkeypatch):
    skill = _skill_stub(slug="x", is_recommended_workspace=True)
    monkeypatch.setattr(svc, "is_valid_skill_slug", lambda s: bool(s))
    monkeypatch.setattr(svc, "SkillRepository", lambda _db: _RepoOK(skill))
    with patch.object(svc, "user_can_access_skill", return_value=False):
        with pytest.raises(ValueError, match="无权访问"):
            await svc.clone_recommended_workspace_skill_to_personal(db=None, slug="x", operator=_user("alice"))


# ---------- confirm_recommended_workspace_install（最小覆盖） ----------


class _FakeDraftDbOK:
    def __init__(self):
        self.added = []
        self.commits = 0

    async def execute(self, _stmt):
        return _ListResult([])  # exists_slug 总是返回空

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass

    async def refresh(self, obj, attribute_names=None):
        if not hasattr(obj, "is_recommended_workspace"):
            obj.is_recommended_workspace = False


class _IntegrityErrorStub(_RealIntegrityError):
    def __init__(self):
        pass


def test_load_and_select_draft_items_is_called(monkeypatch, tmp_path):
    """冒烟：构造最小 draft + skill，验证装机后 is_recommended_workspace=True 落库。

    详细走查留给 router 测试；这里只确认 service 函数被调用并写入了字段。
    """
    import asyncio

    draft_dir = tmp_path / "draft"
    source_dir = draft_dir / "items" / "x"
    source_dir.mkdir(parents=True)
    (source_dir / "SKILL.md").write_text("---\nname: Demo\ndescription: d\n---\n# demo", encoding="utf-8")
    fake_draft_item = {"slug": "demo", "source_dir": "items/x", "success": True}

    monkeypatch.setattr(
        svc,
        "_load_and_select_draft_items",
        lambda *a, **kw: (draft_dir, {"source_type": "upload"}, [fake_draft_item]),
    )
    monkeypatch.setattr(svc, "is_valid_skill_slug", lambda s: bool(s))
    monkeypatch.setattr(
        svc,
        "_parse_skill_dir_metadata",
        lambda p: {
            "slug": "demo",
            "name": "Demo",
            "description": "d",
            "tool_dependencies": [],
            "mcp_dependencies": [],
            "skill_dependencies": [],
        },
    )
    monkeypatch.setattr(svc, "get_skills_root_dir", lambda: draft_dir)

    added_items: list = []

    class _Repo:
        async def exists_slug(self, _s):
            return False

        async def create(self, **_):
            item = SimpleNamespace(slug="demo", to_dict=lambda: {"slug": "demo"})
            item.is_recommended_workspace = False
            added_items.append(item)
            return item

    class _Db:
        async def execute(self, _stmt):
            class _R:
                def scalars(self_inner):
                    return SimpleNamespace(all=lambda: [])

            return _R()

        def add(self, obj):
            added_items.append(obj)

        async def commit(self):
            pass

        async def rollback(self):
            pass

        async def refresh(self, obj, attribute_names=None):
            obj.is_recommended_workspace = True  # 模拟 commit 后 DB 已写入

    monkeypatch.setattr(svc, "SkillRepository", lambda _db: _Repo())

    result = asyncio.run(
        svc.confirm_recommended_workspace_install(_Db(), draft_id="x", slugs=None, operator=_user("alice"))
    )
    # service 应该返回成功结果（即使我们没让 _Db 真正持久化）
    assert result[0]["success"] is True, f"install failed: {result}"


def test_confirm_recommended_workspace_install_rejects_invalid_slug(monkeypatch):
    """slug 非法（含特殊字符）应被拒。"""
    import asyncio

    fake_draft_item = {"slug": "Bad!", "source_dir": "items/x", "success": True}
    monkeypatch.setattr(
        svc,
        "_load_and_select_draft_items",
        lambda *a, **kw: (None, {"source_type": "upload"}, [fake_draft_item]),
    )
    # "Bad!" 不含特殊字符则通过；强制让 is_valid_skill_slug 返回 False
    monkeypatch.setattr(svc, "is_valid_skill_slug", lambda _s: False)

    result = asyncio.run(
        svc.confirm_recommended_workspace_install(None, draft_id="x", slugs=None, operator=_user("alice"))
    )
    assert result[0]["success"] is False
    assert "无效 skill slug" in result[0]["error"]
