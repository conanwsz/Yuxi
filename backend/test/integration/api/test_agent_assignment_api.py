import uuid


async def test_standard_user_manages_only_personal_agent(test_client, standard_user):
    slug = f"pytest-personal-agent-{uuid.uuid4().hex[:8]}"
    headers = standard_user["headers"]

    create_response = await test_client.post(
        "/api/agent",
        headers=headers,
        json={"name": "个人 Agent", "slug": slug, "backend_id": "ChatbotAgent"},
    )
    assert create_response.status_code == 200, create_response.text
    created = create_response.json()["agent"]
    assert created["can_view_config"] is True
    assert created["can_update"] is True
    assert created["can_delete"] is True

    list_response = await test_client.get("/api/agent", headers=headers)
    assert list_response.status_code == 200, list_response.text
    listed = next(item for item in list_response.json()["agents"] if item["slug"] == slug)
    assert listed["can_view_config"] is True
    assert "config_json" not in listed
    assert "share_config" not in listed

    detail_response = await test_client.get(f"/api/agent/{slug}", headers=headers)
    assert detail_response.status_code == 200, detail_response.text
    assert "config_json" in detail_response.json()["agent"]

    delete_response = await test_client.delete(f"/api/agent/{slug}", headers=headers)
    assert delete_response.status_code == 200, delete_response.text


async def test_assigned_user_receives_summary_and_runtime_metadata_only(
    test_client,
    admin_headers,
    standard_user,
):
    slug = f"pytest-assigned-agent-{uuid.uuid4().hex[:8]}"
    target_uid = standard_user["user"]["uid"]
    create_response = await test_client.post(
        "/api/agent",
        headers=admin_headers,
        json={
            "name": "管理员分配 Agent",
            "slug": slug,
            "backend_id": "ChatbotAgent",
            "config_json": {
                "context": {
                    "system_prompt": "不得返回给被分配用户",
                    "tools": [],
                    "knowledges": [],
                    "mcps": [],
                    "skills": [],
                    "subagents": [],
                }
            },
            "share_config": {
                "version": 2,
                "read_scope": {
                    "access_level": "user",
                    "department_ids": [],
                    "user_uids": [target_uid],
                },
                "manage_scope": None,
            },
        },
    )
    assert create_response.status_code == 200, create_response.text

    try:
        list_response = await test_client.get("/api/agent", headers=standard_user["headers"])
        assert list_response.status_code == 200, list_response.text
        listed = next(item for item in list_response.json()["agents"] if item["slug"] == slug)
        assert listed["can_view_config"] is False
        assert listed["can_update"] is False
        assert listed["can_delete"] is False
        assert "config_json" not in listed
        assert "share_config" not in listed

        detail_response = await test_client.get(f"/api/agent/{slug}", headers=standard_user["headers"])
        assert detail_response.status_code == 403, detail_response.text

        update_response = await test_client.put(
            f"/api/agent/{slug}",
            headers=standard_user["headers"],
            json={"description": "越权修改"},
        )
        assert update_response.status_code == 403, update_response.text

        assignment_response = await test_client.get(
            f"/api/agent/{slug}/assignment",
            headers=standard_user["headers"],
        )
        assert assignment_response.status_code == 403, assignment_response.text

        delete_response = await test_client.delete(f"/api/agent/{slug}", headers=standard_user["headers"])
        assert delete_response.status_code == 403, delete_response.text

        runtime_response = await test_client.get(
            f"/api/agent/{slug}/runtime-metadata",
            headers=standard_user["headers"],
        )
        assert runtime_response.status_code == 200, runtime_response.text
        runtime_data = runtime_response.json()
        assert "system_prompt" not in runtime_data["runtime_context"]
    finally:
        delete_response = await test_client.delete(f"/api/agent/{slug}", headers=admin_headers)
        assert delete_response.status_code == 200, delete_response.text


async def test_standard_user_assignment_options_are_limited_to_membership_departments(
    test_client,
    standard_user,
):
    response = await test_client.get("/api/agent/assignment-options", headers=standard_user["headers"])

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["allowed_access_levels"] == ["user"]
    assert data["departments"] == []
    assert standard_user["user"]["uid"] in {item["uid"] for item in data["users"]}
