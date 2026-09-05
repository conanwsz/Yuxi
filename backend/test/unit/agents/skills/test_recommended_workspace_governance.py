"""推荐位治理（下架/重新上架/删除）的 service 层单测。

门控口径：路由级 `skills.recommend` + 对象级 `is_recommended_workspace` 校验，
不要求调用者是技能创建者（与 update_skill_enabled 的 MANAGE 门控有意区分）。
"""

from types import SimpleNamespace

import pytest

from yuxi.agents.skills import service


def _recommended_skill(
    slug: str = "skill-creator",
    *,
    enabled: bool = True,
    recommended: bool = True,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=7,
        slug=slug,
        name=slug,
        enabled=enabled,
        is_recommended_workspace=recommended,
        source_type="upload",
        created_by="alice",
        to_dict=lambda: {"slug": slug, "enabled": enabled, "is_recommended_workspace": recommended},
    )


class RecordingRepo:
    def __init__(self):
        self.enabled_calls: list[tuple] = []

    async def update_enabled(self, item, *, enabled, updated_by):
        self.enabled_calls.append((item.slug, enabled, updated_by))
        return _recommended_skill(item.slug, enabled=enabled)


def _patch_skill(monkeypatch, skill):
    async def fake_get(_db, _slug):
        return skill

    monkeypatch.setattr(service, "get_skill_or_raise", fake_get)


def _patch_repo(monkeypatch, repo):
    monkeypatch.setattr(service, "SkillRepository", lambda _db: repo)


@pytest.mark.asyncio
async def test_set_enabled_deactivates_recommended_skill(monkeypatch):
    skill = _recommended_skill()
    repo = RecordingRepo()
    _patch_skill(monkeypatch, skill)
    _patch_repo(monkeypatch, repo)

    operator = SimpleNamespace(uid="admin")
    data = await service.set_recommended_workspace_skill_enabled(
        None, slug="skill-creator", enabled=False, operator=operator
    )

    assert repo.enabled_calls == [("skill-creator", False, "admin")]
    assert data["enabled"] is False


@pytest.mark.asyncio
async def test_set_enabled_reactivates_deactivated_skill(monkeypatch):
    skill = _recommended_skill(enabled=False)
    repo = RecordingRepo()
    _patch_skill(monkeypatch, skill)
    _patch_repo(monkeypatch, repo)

    operator = SimpleNamespace(uid="admin")
    data = await service.set_recommended_workspace_skill_enabled(
        None, slug="skill-creator", enabled=True, operator=operator
    )

    assert repo.enabled_calls == [("skill-creator", True, "admin")]
    assert data["enabled"] is True


@pytest.mark.asyncio
async def test_set_enabled_rejects_skill_not_in_recommendation(monkeypatch):
    skill = _recommended_skill(recommended=False)
    repo = RecordingRepo()
    _patch_skill(monkeypatch, skill)
    _patch_repo(monkeypatch, repo)

    operator = SimpleNamespace(uid="admin")
    with pytest.raises(ValueError, match="不在推荐工作区"):
        await service.set_recommended_workspace_skill_enabled(
            None, slug="skill-creator", enabled=False, operator=operator
        )
    assert repo.enabled_calls == []


@pytest.mark.asyncio
async def test_delete_recommended_skill_deletes_without_ownership_check(monkeypatch):
    # 推荐位技能的创建者是 alice，治理接口不接受 operator，天然无创建者校验
    skill = _recommended_skill()
    _patch_skill(monkeypatch, skill)
    calls: list[str] = []

    async def fake_delete(_db, *, slug):
        calls.append(slug)

    monkeypatch.setattr(service, "delete_skill", fake_delete)

    await service.delete_recommended_workspace_skill(None, slug="skill-creator")
    assert calls == ["skill-creator"]


@pytest.mark.asyncio
async def test_delete_rejects_skill_not_in_recommendation(monkeypatch):
    skill = _recommended_skill(recommended=False)
    _patch_skill(monkeypatch, skill)
    calls: list[str] = []

    async def fake_delete(_db, *, slug):
        calls.append(slug)

    monkeypatch.setattr(service, "delete_skill", fake_delete)

    with pytest.raises(ValueError, match="不在推荐工作区"):
        await service.delete_recommended_workspace_skill(None, slug="skill-creator")
    assert calls == []
