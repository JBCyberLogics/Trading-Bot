from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_protected_endpoints_require_auth(async_client):
    response = await async_client.get("/api/accounts")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_logout_flow(async_client, test_env: dict):
    login = await async_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": test_env["password"]},
    )
    assert login.status_code == 200
    token = login.json()["token"]

    headers = {"Authorization": f"Bearer {token}"}
    ok = await async_client.get("/api/status", headers=headers)
    assert ok.status_code == 200

    logout = await async_client.post("/api/auth/logout", headers=headers, json={})
    assert logout.status_code == 200

    denied = await async_client.get("/api/status", headers=headers)
    assert denied.status_code == 401
