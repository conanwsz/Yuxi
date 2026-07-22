from types import SimpleNamespace

import pytest

from yuxi.agents.skills.service import can_skill_depend_on


class FakeResult:
    def all(self):
        return [
            (1, 1),
            (2, 1),
            (2, 2),
            (3, 1),
            (3, 2),
            (3, 3),
            (4, 4),
        ]


class FakeDb:
    async def execute(self, _query):
        return FakeResult()


def skill(share_config, *, enabled=True):
    return SimpleNamespace(source_type="upload", enabled=enabled, share_config=share_config)


@pytest.mark.asyncio
async def test_department_dependency_may_cover_a_broader_subtree():
    parent = skill(
        {
            "access_level": "department",
            "org_scope_version": 2,
            "department_ids": [1],
            "excluded_department_ids": [2],
            "user_uids": [],
        }
    )
    dependency = skill(
        {
            "access_level": "department",
            "org_scope_version": 2,
            "department_ids": [1],
            "excluded_department_ids": [3],
            "user_uids": [],
        }
    )

    assert await can_skill_depend_on(FakeDb(), parent, dependency) is True


@pytest.mark.asyncio
async def test_department_dependency_rejects_a_narrower_subtree():
    parent = skill(
        {
            "access_level": "department",
            "org_scope_version": 2,
            "department_ids": [1],
            "excluded_department_ids": [],
            "user_uids": [],
        }
    )
    dependency = skill(
        {
            "access_level": "department",
            "org_scope_version": 2,
            "department_ids": [1],
            "excluded_department_ids": [2],
            "user_uids": [],
        }
    )

    assert await can_skill_depend_on(FakeDb(), parent, dependency) is False


@pytest.mark.asyncio
async def test_department_dependency_covers_explicit_user_overrides():
    parent = skill(
        {
            "access_level": "department",
            "org_scope_version": 2,
            "department_ids": [1],
            "excluded_department_ids": [],
            "user_uids": ["u-1"],
        }
    )
    dependency = skill(
        {
            "access_level": "department",
            "org_scope_version": 2,
            "department_ids": [1],
            "excluded_department_ids": [],
            "user_uids": [],
        }
    )

    assert await can_skill_depend_on(FakeDb(), parent, dependency) is False
