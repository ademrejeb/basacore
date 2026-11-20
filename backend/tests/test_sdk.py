from __future__ import annotations

import uuid

from httpx import AsyncClient

from tests.conftest import API_SECRET


async def test_oauth_rejects_wrong_secret_and_scope_escalation(client: AsyncClient, fixture_data) -> None:
    bad = await client.post(
        "/v1/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": str(fixture_data.credential_id),
            "client_secret": "not-the-secret",
        },
    )
    assert bad.status_code == 401

    escalation = await client.post(
        "/v1/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": str(fixture_data.credential_id),
            "client_secret": API_SECRET,
            "scope": "platform:root",
        },
    )
    assert escalation.status_code == 422
    assert escalation.json()["error"]["code"] == "validation_failed"


async def test_sdk_tenant_resolution_is_limited_to_own_application(
    client: AsyncClient, fixture_data, service_headers: dict[str, str]
) -> None:
    own = await client.get(
        "/v1/app/tenants/resolve",
        params={"slug": fixture_data.tenant_a_slug},
        headers=service_headers,
    )
    assert own.status_code == 200
    assert own.json()["id"] == str(fixture_data.tenant_a_id)

    missing = await client.get(
        "/v1/app/tenants/resolve",
        params={"slug": "does-not-exist"},
        headers=service_headers,
    )
    assert missing.status_code == 404


async def test_sdk_authorization_check_uses_real_grants(
    client: AsyncClient,
    fixture_data,
    service_headers: dict[str, str],
    tenant_a_headers: dict[str, str],
) -> None:
    me = await client.get("/v1/auth/me", headers=service_headers)
    # Service tokens are not user tokens: /auth/me must not accept them.
    assert me.status_code == 401

    tenant_user = await client.get("/v1/auth/me", headers=tenant_a_headers)
    assert tenant_user.status_code == 200
    user_id = tenant_user.json()["user"]["id"]

    allowed = await client.post(
        "/v1/app/authorization/check",
        headers=service_headers,
        json={
            "user_id": user_id,
            "tenant_id": str(fixture_data.tenant_a_id),
            "permission": "tenants.read",
        },
    )
    assert allowed.status_code == 200
    assert allowed.json()["authorized"] is True
    assert "tenants.read" in allowed.json()["effective_permissions"]

    denied = await client.post(
        "/v1/app/authorization/check",
        headers=service_headers,
        json={
            "user_id": user_id,
            "tenant_id": str(fixture_data.tenant_a_id),
            "permission": "applications.create",
        },
    )
    assert denied.status_code == 200
    assert denied.json()["authorized"] is False
    assert denied.json()["reason"] == "missing_permission"

    not_a_member = await client.post(
        "/v1/app/authorization/check",
        headers=service_headers,
        json={
            "user_id": user_id,
            "tenant_id": str(fixture_data.tenant_b_id),
            "permission": "tenants.read",
        },
    )
    assert not_a_member.status_code == 200
    assert not_a_member.json()["authorized"] is False
    assert not_a_member.json()["reason"] == "not_a_member"


async def test_sdk_quota_enforcement_and_idempotency(
    client: AsyncClient, fixture_data, service_headers: dict[str, str]
) -> None:
    key = f"test-{uuid.uuid4().hex}"
    first = await client.post(
        "/v1/app/usage",
        headers=service_headers,
        json={
            "tenant_id": str(fixture_data.tenant_a_id),
            "metric": "api_calls",
            "value": 999,
            "idempotency_key": key,
        },
    )
    assert first.status_code == 201
    assert first.json()["quantity"] == 999

    replay = await client.post(
        "/v1/app/usage",
        headers=service_headers,
        json={
            "tenant_id": str(fixture_data.tenant_a_id),
            "metric": "api_calls",
            "value": 999,
            "idempotency_key": key,
        },
    )
    assert replay.status_code == 201
    assert replay.json()["idempotent"] is True

    over = await client.post(
        "/v1/app/usage",
        headers=service_headers,
        json={
            "tenant_id": str(fixture_data.tenant_a_id),
            "metric": "api_calls",
            "value": 2,
        },
    )
    assert over.status_code == 402
    assert over.json()["error"]["code"] == "quota_exceeded"
    assert over.json()["error"]["details"]["limit"] == 1000


async def test_sdk_audit_write_is_queryable_by_platform_auditors(
    client: AsyncClient, fixture_data, service_headers: dict[str, str], admin_headers: dict[str, str]
) -> None:
    written = await client.post(
        "/v1/app/audit",
        headers=service_headers,
        json={
            "action": "test.sdk_event",
            "resource_type": "test",
            "resource_id": "sdk-1",
            "tenant_id": str(fixture_data.tenant_a_id),
            "metadata": {"source": "test"},
        },
    )
    assert written.status_code == 201

    queried = await client.get("/v1/audit-logs", headers=admin_headers, params={"action": "test.sdk_event"})
    assert queried.status_code == 200
    assert queried.json()["total"] >= 1
    assert queried.json()["items"][0]["actor_email"].startswith("service:")
