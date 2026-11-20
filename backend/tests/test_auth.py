from __future__ import annotations

import pytest
from httpx import AsyncClient

from tests.conftest import PASSWORD, login


async def test_unauthenticated_request_is_rejected(client: AsyncClient) -> None:
    response = await client.get("/v1/tenants")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_login_rejects_bad_password(client: AsyncClient, fixture_data) -> None:
    response = await login(client, fixture_data.admin_email, "Wrong!Password#2026")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


async def test_login_returns_identity_and_platform_permissions(client: AsyncClient, fixture_data) -> None:
    response = await login(client, fixture_data.admin_email)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "authenticated"
    assert body["user"]["email"] == fixture_data.admin_email
    token = body["tokens"]["access_token"]

    me = await client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    payload = me.json()
    assert "tenants.read" in payload["platform_permissions"]
    assert payload["scopes"][0]["scope_type"] == "platform"


async def test_refresh_rotation_and_reuse_detection(client: AsyncClient, fixture_data) -> None:
    response = await login(client, fixture_data.admin_email)
    assert response.status_code == 200
    original = response.json()["tokens"]["refresh_token"]

    rotated = await client.post("/v1/auth/refresh", json={"refresh_token": original})
    assert rotated.status_code == 200
    assert rotated.json()["refresh_token"] != original
    assert rotated.json()["access_token"]

    reused = await client.post("/v1/auth/refresh", json={"refresh_token": original})
    assert reused.status_code == 401
    assert reused.json()["error"]["code"] == "unauthorized"


async def test_logout_revokes_the_session(client: AsyncClient, fixture_data) -> None:
    response = await login(client, fixture_data.admin_email)
    tokens = response.json()["tokens"]
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    logged_out = await client.post("/v1/auth/logout", headers=headers)
    assert logged_out.status_code == 204

    me = await client.get("/v1/auth/me", headers=headers)
    assert me.status_code == 401


@pytest.mark.parametrize(
    "path",
    [
        "/v1/tenants",
        "/v1/organizations",
        "/v1/applications",
        "/v1/billing/subscriptions",
        "/v1/audit-logs",
    ],
)
async def test_tenant_user_cannot_reach_platform_endpoints(
    client: AsyncClient, tenant_a_headers: dict[str, str], path: str
) -> None:
    response = await client.get(path, headers=tenant_a_headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


async def test_password_policy_is_enforced(client: AsyncClient, fixture_data) -> None:
    response = await login(client, fixture_data.admin_email)
    token = response.json()["tokens"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    weak = await client.post(
        "/v1/auth/password/change",
        headers=headers,
        json={"current_password": PASSWORD, "new_password": "short"},
    )
    assert weak.status_code == 422
    assert weak.json()["error"]["code"] == "validation_failed"
