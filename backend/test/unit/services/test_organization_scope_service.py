from types import SimpleNamespace

from yuxi.services.organization_scope_service import (
    share_config_allows_user,
    user_can_manage_department,
)


def make_user(*, department_id=3, paths=None, managed=None, uid="u1", role="user"):
    return SimpleNamespace(
        uid=uid,
        role=role,
        department_id=department_id,
        organization_department_ids={department_id},
        organization_membership_paths=paths or [{1, 2, department_id}],
        managed_department_ids=set(managed or []),
    )


def test_v1_department_scope_remains_exact_match():
    user = make_user()

    assert not share_config_allows_user(
        user,
        {"access_level": "department", "department_ids": [1], "user_uids": []},
    )
    assert share_config_allows_user(
        user,
        {"access_level": "department", "department_ids": [3], "user_uids": []},
    )


def test_v2_department_scope_inherits_and_excludes_subtree():
    user = make_user()
    config = {
        "access_level": "department",
        "org_scope_version": 2,
        "department_ids": [1],
        "excluded_department_ids": [2],
        "user_uids": [],
    }

    assert not share_config_allows_user(user, config)

    config["excluded_department_ids"] = [4]
    assert share_config_allows_user(user, config)


def test_explicit_user_override_wins_over_department_exclusion():
    user = make_user(uid="override-user")
    config = {
        "access_level": "department",
        "org_scope_version": 2,
        "department_ids": [1],
        "excluded_department_ids": [2],
        "user_uids": ["override-user"],
    }

    assert share_config_allows_user(user, config)


def test_any_allowed_membership_grants_access():
    user = make_user(paths=[{1, 2, 3}, {1, 5, 6}])
    config = {
        "access_level": "department",
        "org_scope_version": 2,
        "department_ids": [1],
        "excluded_department_ids": [2],
        "user_uids": [],
    }

    assert share_config_allows_user(user, config)


def test_management_scope_is_explicit_and_preexpanded():
    user = make_user(managed={7, 8, 9})

    assert user_can_manage_department(user, 8)
    assert not user_can_manage_department(user, 3)
