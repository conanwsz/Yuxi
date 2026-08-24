from __future__ import annotations

import os
import uuid

import psycopg
import pytest

from yuxi.agents.backends.sandbox.provider import load_user_agent_env


pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

AGENT_ENV_PATH = "/api/user/agent-env"


def _postgres_url() -> str:
    return os.environ["POSTGRES_URL"].replace("+asyncpg", "").replace("+psycopg", "")


def _bind_oidc_identity(user: dict) -> None:
    with psycopg.connect(_postgres_url()) as conn, conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO external_identities (issuer, subject, user_id, email)
            VALUES (%s, %s, %s, %s)
            """,
            (
                f"https://pytest-{uuid.uuid4().hex}.example.com",
                uuid.uuid4().hex,
                user["id"],
                f"{user['uid']}@example.com",
            ),
        )


async def test_agent_env_requires_auth(test_client):
    response = await test_client.get(AGENT_ENV_PATH)
    assert response.status_code == 401


async def test_agent_env_round_trip_and_replace(test_client, standard_user):
    headers = standard_user["headers"]

    initial_response = await test_client.get(AGENT_ENV_PATH, headers=headers)
    assert initial_response.status_code == 200, initial_response.text
    assert initial_response.json()["env"] == {}

    payload = {"env": {"YUXI_TEST_TOKEN": "secret", "YUXI_EMPTY_VALUE": ""}}
    save_response = await test_client.put(AGENT_ENV_PATH, json=payload, headers=headers)
    assert save_response.status_code == 200, save_response.text
    assert save_response.json()["env"] == payload["env"]

    replace_response = await test_client.put(
        AGENT_ENV_PATH,
        json={"env": {"YUXI_TEST_TOKEN": "updated"}},
        headers=headers,
    )
    assert replace_response.status_code == 200, replace_response.text
    assert replace_response.json()["env"] == {"YUXI_TEST_TOKEN": "updated"}

    final_response = await test_client.get(AGENT_ENV_PATH, headers=headers)
    assert final_response.status_code == 200, final_response.text
    assert final_response.json()["env"] == {"YUXI_TEST_TOKEN": "updated"}


async def test_agent_env_rejects_invalid_keys(test_client, standard_user):
    response = await test_client.put(
        AGENT_ENV_PATH,
        json={"env": {"INVALID-KEY": "value"}},
        headers=standard_user["headers"],
    )
    assert response.status_code == 400


async def test_agent_env_rejects_duplicate_normalized_keys(test_client, standard_user):
    response = await test_client.put(
        AGENT_ENV_PATH,
        json={"env": {"YUXI_TOKEN": "first", " YUXI_TOKEN ": "second"}},
        headers=standard_user["headers"],
    )
    assert response.status_code == 400


async def test_agent_env_is_user_scoped(test_client, standard_user, admin_headers):
    standard_headers = standard_user["headers"]
    user_payload = {"env": {"YUXI_USER_ONLY": "user-value"}}

    user_save_response = await test_client.put(AGENT_ENV_PATH, json=user_payload, headers=standard_headers)
    assert user_save_response.status_code == 200, user_save_response.text

    admin_response = await test_client.get(AGENT_ENV_PATH, headers=admin_headers)
    assert admin_response.status_code == 200, admin_response.text
    assert admin_response.json()["env"] != user_payload["env"]

    user_response = await test_client.get(AGENT_ENV_PATH, headers=standard_headers)
    assert user_response.status_code == 200, user_response.text
    assert user_response.json()["env"] == user_payload["env"]


async def test_existing_oidc_user_gets_readonly_uid_env(test_client, standard_user):
    user = standard_user["user"]
    _bind_oidc_identity(user)

    response = await test_client.get(AGENT_ENV_PATH, headers=standard_user["headers"])

    assert response.status_code == 200, response.text
    assert response.json()["env"]["uid"] == user["uid"]
    assert response.json()["readonly_keys"] == ["uid"]


async def test_oidc_uid_env_cannot_be_overridden_or_deleted(test_client, standard_user):
    user = standard_user["user"]
    _bind_oidc_identity(user)

    override_response = await test_client.put(
        AGENT_ENV_PATH,
        json={"env": {"uid": "spoofed"}},
        headers=standard_user["headers"],
    )
    assert override_response.status_code == 400, override_response.text

    save_response = await test_client.put(
        AGENT_ENV_PATH,
        json={"env": {"YUXI_USER_VALUE": "saved"}},
        headers=standard_user["headers"],
    )
    assert save_response.status_code == 200, save_response.text
    assert save_response.json()["env"] == {"YUXI_USER_VALUE": "saved", "uid": user["uid"]}
    assert save_response.json()["readonly_keys"] == ["uid"]
    assert load_user_agent_env(user["uid"]) == {"YUXI_USER_VALUE": "saved", "uid": user["uid"]}

    with psycopg.connect(_postgres_url()) as conn, conn.cursor() as cursor:
        cursor.execute("SELECT env FROM agent_envs WHERE uid = %s", (user["uid"],))
        stored = cursor.fetchone()
    assert stored is not None
    assert stored[0] == {"YUXI_USER_VALUE": "saved"}
