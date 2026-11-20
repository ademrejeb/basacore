from __future__ import annotations

import uuid

from httpx import AsyncClient


def _slug(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


async def test_organization_onboarding_and_updates(
    client: AsyncClient, fixture_data, admin_headers: dict[str, str]
) -> None:
    slug = _slug("onboard-org")
    created = await client.post(
        "/v1/organizations",
        headers=admin_headers,
        json={
            "name": "Onboarded Organization",
            "slug": slug,
            "description": "Created by the admin test suite",
            "billing_email": "billing@example.dev",
        },
    )
    assert created.status_code == 201, created.text
    organization = created.json()
    assert organization["slug"] == slug
    assert organization["status"] == "active"

    duplicate = await client.post(
        "/v1/organizations",
        headers=admin_headers,
        json={"name": "Duplicate", "slug": slug},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "conflict"

    detail = await client.get(f"/v1/organizations/{organization['id']}", headers=admin_headers)
    assert detail.status_code == 200
    assert detail.json()["counts"]["applications"] == 0
    assert detail.json()["billing_email"] == "billing@example.dev"

    updated = await client.patch(
        f"/v1/organizations/{organization['id']}",
        headers=admin_headers,
        json={"name": "Renamed Organization", "description": "Updated"},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Renamed Organization"

    listed = await client.get("/v1/organizations", headers=admin_headers, params={"search": slug})
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["id"] == organization["id"]


async def test_application_onboarding_with_environment_config_and_credential(
    client: AsyncClient, fixture_data, admin_headers: dict[str, str]
) -> None:
    app_slug = _slug("onboard-app")
    created = await client.post(
        "/v1/applications",
        headers=admin_headers,
        json={
            "organization_id": str(fixture_data.organization_id),
            "name": "Onboarded Application",
            "slug": app_slug,
            "description": "Application onboarding flow",
            "owner_id": str(fixture_data.admin_id),
            "base_url": "https://app.example.dev",
            "auth_config": {"mfa_required": True},
            "integration_config": {"webhooks": True},
            "resource_allocation": {"region": "eu-west-1"},
        },
    )
    assert created.status_code == 201, created.text
    application = created.json()
    application_id = application["id"]
    assert application["slug"] == app_slug
    assert application["status"] == "draft"
    assert application["environment"]["slug"] == "production"

    environments = await client.get(f"/v1/applications/{application_id}/environments", headers=admin_headers)
    assert environments.status_code == 200
    assert len(environments.json()) == 1

    staging = await client.post(
        f"/v1/applications/{application_id}/environments",
        headers=admin_headers,
        json={
            "name": "Staging",
            "slug": "staging",
            "kind": "staging",
            "base_url": "https://staging.example.dev",
            "api_base_url": "https://staging-api.example.dev",
            "config": {"debug": True},
        },
    )
    assert staging.status_code == 201, staging.text
    assert staging.json()["kind"] == "staging"

    published = await client.post(
        f"/v1/applications/{application_id}/config",
        headers=admin_headers,
        json={
            "config": {"feature": "on", "nested": {"limit": 5}},
            "environment_id": staging.json()["id"],
            "note": "Initial staging configuration",
        },
    )
    assert published.status_code == 201, published.text
    assert published.json()["version"] == 1

    current = await client.get(
        f"/v1/applications/{application_id}/config",
        headers=admin_headers,
        params={"environment_id": staging.json()["id"]},
    )
    assert current.status_code == 200
    assert current.json()["environment"]["version"] == 1
    assert current.json()["effective"]["feature"] == "on"

    credential = await client.post(
        f"/v1/applications/{application_id}/credentials",
        headers=admin_headers,
        json={
            "name": "CI credential",
            "scopes": ["app:config.read", "app:usage.write"],
            "expires_in_days": 30,
        },
    )
    assert credential.status_code == 201, credential.text
    issued = credential.json()
    assert issued["client_id"] == issued["id"]
    assert issued["client_secret"].startswith("bsk_")
    assert issued["warning"]

    listed = await client.get(f"/v1/applications/{application_id}/credentials", headers=admin_headers)
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert "client_secret" not in listed.json()[0]
    assert "secret_hash" not in listed.json()[0]


async def test_application_activation_requires_base_url_and_environment(
    client: AsyncClient, fixture_data, admin_headers: dict[str, str]
) -> None:
    created = await client.post(
        "/v1/applications",
        headers=admin_headers,
        json={
            "organization_id": str(fixture_data.organization_id),
            "name": "Activation Candidate",
            "slug": _slug("activation"),
        },
    )
    assert created.status_code == 201, created.text
    application_id = created.json()["id"]

    blocked = await client.post(
        f"/v1/applications/{application_id}/status",
        headers=admin_headers,
        json={"status": "active", "reason": "Attempt without base URL"},
    )
    assert blocked.status_code == 422
    assert blocked.json()["error"]["code"] == "validation_failed"

    patched = await client.patch(
        f"/v1/applications/{application_id}",
        headers=admin_headers,
        json={"base_url": "https://activation.example.dev"},
    )
    assert patched.status_code == 200

    activated = await client.post(
        f"/v1/applications/{application_id}/status",
        headers=admin_headers,
        json={"status": "active", "reason": "Ready for production"},
    )
    assert activated.status_code == 200, activated.text
    assert activated.json()["status"] == "active"


async def test_tenant_provisioning_and_lifecycle(
    client: AsyncClient, fixture_data, admin_headers: dict[str, str]
) -> None:
    tenant_slug = _slug("onboard-tenant")
    owner_email = f"owner-{uuid.uuid4().hex[:8]}@example.dev"
    domain = f"{tenant_slug}.example.dev"

    created = await client.post(
        "/v1/tenants",
        headers=admin_headers,
        json={
            "application_id": str(fixture_data.application_id),
            "organization_id": str(fixture_data.organization_id),
            "name": "Onboarded Tenant",
            "slug": tenant_slug,
            "owner_email": owner_email,
            "plan_id": str(fixture_data.plan_id),
            "billing_interval": "monthly",
            "trial_days": 14,
            "custom_domain": domain,
            "quotas": {"api_calls": 2000, "seats": 5},
            "configuration": {"locale": "en-GB"},
            "branding": {"primary_color": "#123456"},
        },
    )
    assert created.status_code == 201, created.text
    tenant = created.json()
    tenant_id = tenant["id"]
    assert tenant["status"] == "trial"
    assert tenant["slug"] == tenant_slug
    assert tenant["invite_token"]
    assert tenant["subscription"]["plan_id"] == str(fixture_data.plan_id)
    assert tenant["subscription"]["status"] == "trialing"
    assert tenant["domains"][0]["domain"] == domain
    assert tenant["counts"]["users"] == 1
    assert tenant["users"][0]["email"] == owner_email
    assert "tenant_owner" in tenant["users"][0]["roles"]

    duplicate = await client.post(
        "/v1/tenants",
        headers=admin_headers,
        json={
            "application_id": str(fixture_data.application_id),
            "organization_id": str(fixture_data.organization_id),
            "name": "Duplicate Tenant",
            "slug": tenant_slug,
        },
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "conflict"

    config = await client.get(f"/v1/tenants/{tenant_id}/config", headers=admin_headers)
    assert config.status_code == 200
    assert config.json()["current"]["config"]["locale"] == "en-GB"

    suspended = await client.post(
        f"/v1/tenants/{tenant_id}/suspend",
        headers=admin_headers,
        json={"reason": "Payment review"},
    )
    assert suspended.status_code == 200
    assert suspended.json()["status"] == "suspended"

    reactivated = await client.post(
        f"/v1/tenants/{tenant_id}/reactivate",
        headers=admin_headers,
        json={"reason": "Review complete"},
    )
    assert reactivated.status_code == 200
    assert reactivated.json()["status"] == "trial"

    archived = await client.post(
        f"/v1/tenants/{tenant_id}/archive",
        headers=admin_headers,
        json={"reason": "Customer churned"},
    )
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"

    wrong_confirmation = await client.post(
        f"/v1/tenants/{tenant_id}/delete",
        headers=admin_headers,
        json={"confirm_name": "Not The Tenant", "reason": "Cleanup"},
    )
    assert wrong_confirmation.status_code == 422

    deleted = await client.post(
        f"/v1/tenants/{tenant_id}/delete",
        headers=admin_headers,
        json={"confirm_name": "Onboarded Tenant", "reason": "Cleanup"},
    )
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "deleted"


async def test_tenant_user_cannot_provision_tenants(
    client: AsyncClient, fixture_data, tenant_a_headers: dict[str, str]
) -> None:
    response = await client.post(
        "/v1/tenants",
        headers=tenant_a_headers,
        json={
            "application_id": str(fixture_data.application_id),
            "organization_id": str(fixture_data.organization_id),
            "name": "Escalated Tenant",
        },
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


async def test_billing_usage_report_exposes_tenant_quota(
    client: AsyncClient,
    fixture_data,
    admin_headers: dict[str, str],
    tenant_a_headers: dict[str, str],
) -> None:
    response = await client.get("/v1/billing/usage", headers=admin_headers)
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["tenant_count"] >= 2
    assert report["period_start"]

    tenant = next(item for item in report["tenants"] if item["id"] == str(fixture_data.tenant_a_id))
    metrics = {line["metric"]: line for line in tenant["metrics"]}
    assert metrics["api_calls"]["limit"] == 1000
    assert metrics["api_calls"]["used"] == 0
    assert metrics["api_calls"]["over_limit"] is False

    filtered = await client.get(
        "/v1/billing/usage",
        headers=admin_headers,
        params={"application_id": str(fixture_data.application_id)},
    )
    assert filtered.status_code == 200
    assert all(
        item["application_id"] == str(fixture_data.application_id) for item in filtered.json()["tenants"]
    )

    forbidden = await client.get("/v1/billing/usage", headers=tenant_a_headers)
    assert forbidden.status_code == 403
