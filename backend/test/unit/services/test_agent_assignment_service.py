from types import SimpleNamespace

import pytest

from yuxi.services import agent_assignment_service


@pytest.mark.asyncio
async def test_department_admin_assignment_merge_preserves_out_of_scope_targets(monkeypatch):
    async def fake_options(_db, _user):
        return {
            "allowed_access_levels": ["department", "user"],
            "departments": [{"id": 10, "name": "A"}],
            "users": [{"uid": "user-a", "username": "A"}],
        }

    monkeypatch.setattr(agent_assignment_service, "get_agent_assignment_options", fake_options)
    user = SimpleNamespace(role="admin")
    current = {
        "version": 2,
        "read_scope": {
            "access_level": "department",
            "department_ids": [10, 20],
            "user_uids": ["user-a", "user-b"],
        },
        "manage_scope": {"access_level": "user", "user_uids": ["legacy-manager"]},
    }

    merged = await agent_assignment_service.merge_agent_assignment(
        object(),
        user=user,
        current_share_config=current,
        global_access=False,
        department_ids=[],
        user_uids=[],
        owner_uid="owner",
    )

    assert merged == {
        "version": 2,
        "read_scope": {
            "access_level": "department",
            "department_ids": [20],
            "user_uids": ["user-b"],
        },
        "manage_scope": None,
    }


@pytest.mark.asyncio
async def test_department_admin_cannot_assign_outside_available_scope(monkeypatch):
    async def fake_options(_db, _user):
        return {
            "allowed_access_levels": ["department", "user"],
            "departments": [{"id": 10, "name": "A"}],
            "users": [],
        }

    monkeypatch.setattr(agent_assignment_service, "get_agent_assignment_options", fake_options)

    with pytest.raises(ValueError, match="超出当前管理范围"):
        await agent_assignment_service.merge_agent_assignment(
            object(),
            user=SimpleNamespace(role="admin"),
            current_share_config={
                "version": 2,
                "read_scope": {"access_level": "department", "department_ids": [10]},
                "manage_scope": None,
            },
            global_access=False,
            department_ids=[20],
            user_uids=[],
            owner_uid="owner",
        )


@pytest.mark.asyncio
async def test_user_without_share_permission_can_only_keep_personal_assignment():
    user = SimpleNamespace(uid="owner", role="user", permission_keys=set())

    result = await agent_assignment_service.validate_new_agent_assignment(
        object(),
        user=user,
        share_config=None,
    )

    assert result["read_scope"] == {
        "access_level": "user",
        "department_ids": [],
        "user_uids": ["owner"],
    }

    with pytest.raises(ValueError, match="无权分配"):
        await agent_assignment_service.validate_new_agent_assignment(
            object(),
            user=user,
            share_config={
                "version": 2,
                "read_scope": {"access_level": "user", "user_uids": ["other"]},
                "manage_scope": None,
            },
        )
