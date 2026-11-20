"""Regression tests for the hardening pass.

Each test pins a specific failure mode that existed before:

* a delegated administrator could hand out authority it did not hold;
* the console's "disable MFA" request did not match the API schema (422);
* an unverified custom domain resolved a tenant over the integration API;
* the security policy table had no write path, so configuration was inert.
"""

from __future__ import annotations

import uuid

from httpx import AsyncClient
from sqlalchemy import select

from app.core.db import SessionLocal, platform_scope, utcnow
from app.core.enums import DomainStatus, ScopeType, UserStatus, UserType
from app.core.security import hash_password
from app.modules.access.models import Role, UserRole
from app.modules.identity.models import User
from app.modules.tenants.models import TenantDomain
from tests.conftest import PASSWORD, login


async def _create_platform_user(role_key: str) -> tuple[uuid.UUID, str]:
    """Create an active internal user holding a single platform role."""
    suffix = uuid.uuid4().hex[:10]
    async with SessionLocal() as db:
        role = (await db.execute(select(Role).where(Role.key == role_key))).scalar_one()
        user = User(
            email=f"delegated-{suffix}@basa.dev",
            full_name="Delegated Administrator",
            user_type=UserType.INTERNAL.value,
            status=UserStatus.ACTIVE.value,
            password_hash=hash_password(PASSWORD),
        )
        db.add(user)
        await db.flush()
        db.add(
            UserRole(
                user_id=user.id,
                role_id=role.id,
                scope_type=ScopeType.PLATFORM.value,
                scope_id=None,
                created_at=utcnow(),
            )
        )
        await db.commit()
        return user.id, user.email


async def test_delegated_admin_cannot_mint_permissions_it_lacks(client: AsyncClient, fixture_data) -> None:
    # platform_admin deliberately excludes settings.manage and secrets.manage,
    # yet it holds roles.manage: that combination used to be a ladder to
    # super_admin.
    _, email = await _create_platform_user("platform_admin")
    response = await login(client, email)
    assert response.status_code == 200, response.text
    headers = {"Authorization": f"Bearer {response.json()['tokens']['access_token']}"}

    escalated = await client.post(
        "/v1/access/roles",
        headers=headers,
        json={
            "key": f"shadow-{uuid.uuid4().hex[:6]}",
            "name": "Shadow administrator",
            "scope_type": "platform",
            "permissions": ["settings.manage"],
        },
    )
    assert escalated.status_code == 403, escalated.text
    assert escalated.json()["error"]["code"] == "forbidden"
    # The ceiling is what rejected it, not a missing permission: this actor does
    # hold roles.manage and could otherwise create the role.
    assert "does not hold" in escalated.json()["error"]["message"]

    takeover = await client.get("/v1/auth/me", headers=headers)
    user_id = takeover.json()["user"]["id"]
    assigned = await client.post(
        f"/v1/users/{user_id}/roles",
        headers=headers,
        json={"role_key": "super_admin", "scope_type": "platform"},
    )
    assert assigned.status_code == 403, assigned.text
    assert "do not hold" in assigned.json()["error"]["message"]


async def test_disable_mfa_accepts_only_the_current_password(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    response = await client.post(
        "/v1/auth/mfa/disable",
        headers=admin_headers,
        json={"current_password": PASSWORD},
    )
    assert response.status_code == 204, response.text


async def _attach_domain(tenant_id: uuid.UUID, domain: str, verified: bool) -> None:
    async with SessionLocal() as db:
        async with platform_scope(db):
            db.add(
                TenantDomain(
                    tenant_id=tenant_id,
                    domain=domain,
                    is_primary=False,
                    status=(DomainStatus.VERIFIED.value if verified else DomainStatus.PENDING.value),
                    verification_token=f"basa-verify={uuid.uuid4().hex}",
                    verified_at=utcnow() if verified else None,
                    created_at=utcnow(),
                )
            )
            await db.commit()


async def test_unverified_domain_cannot_resolve_a_tenant(
    client: AsyncClient, fixture_data, service_headers: dict[str, str]
) -> None:
    suffix = uuid.uuid4().hex[:8]
    pending = f"pending-{suffix}.example.dev"
    verified = f"verified-{suffix}.example.dev"
    await _attach_domain(fixture_data.tenant_a_id, pending, verified=False)
    await _attach_domain(fixture_data.tenant_a_id, verified, verified=True)

    blocked = await client.get("/v1/app/tenants/resolve", headers=service_headers, params={"domain": pending})
    assert blocked.status_code == 404, blocked.text

    allowed = await client.get(
        "/v1/app/tenants/resolve", headers=service_headers, params={"domain": verified}
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["id"] == str(fixture_data.tenant_a_id)


async def test_expired_invite_can_be_reissued(
    client: AsyncClient, fixture_data, admin_headers: dict[str, str]
) -> None:
    email = f"invitee-{uuid.uuid4().hex[:8]}@example.dev"
    created = await client.post(
        f"/v1/tenants/{fixture_data.tenant_a_id}/users",
        headers=admin_headers,
        json={"email": email, "role_key": "tenant_member"},
    )
    assert created.status_code == 201, created.text
    user_id = created.json()["user_id"]
    first_token = created.json()["invite_token"]
    assert first_token

    # verification_tokens is unique on (user_id, purpose): a resend used to fail
    # with a 500 instead of replacing the token.
    for _ in range(2):
        resent = await client.post(
            f"/v1/tenants/{fixture_data.tenant_a_id}/users/{user_id}/invite",
            headers=admin_headers,
        )
        assert resent.status_code == 200, resent.text
        assert resent.json()["invite_token"] != first_token
        first_token = resent.json()["invite_token"]


async def test_security_policy_can_be_written_and_is_merged(
    client: AsyncClient, admin_headers: dict[str, str]
) -> None:
    updated = await client.put(
        "/v1/access/security-policy",
        headers=admin_headers,
        json={"scope_type": "platform", "settings": {"password_history": 4}},
    )
    assert updated.status_code == 200, updated.text

    effective = await client.get("/v1/access/security-policy", headers=admin_headers)
    assert effective.status_code == 200
    settings = effective.json()["settings"]
    assert settings["password_history"] == 4
    # Unspecified controls keep their defaults instead of disappearing.
    assert settings["max_failed_attempts"] == 5

    unknown = await client.put(
        "/v1/access/security-policy",
        headers=admin_headers,
        json={"scope_type": "platform", "settings": {"not_a_control": True}},
    )
    assert unknown.status_code == 422


async def test_delegated_admin_cannot_create_a_super_admin_user(client: AsyncClient, fixture_data) -> None:
    _, email = await _create_platform_user("platform_admin")
    response = await login(client, email)
    assert response.status_code == 200, response.text
    headers = {"Authorization": f"Bearer {response.json()['tokens']['access_token']}"}

    escalated = await client.post(
        "/v1/users",
        headers=headers,
        json={
            "email": f"shadow-{uuid.uuid4().hex[:8]}@example.dev",
            "full_name": "Shadow User",
            "password": PASSWORD,
            "user_type": "internal",
            "role_keys": ["super_admin"],
        },
    )
    assert escalated.status_code == 403, escalated.text
    assert "do not hold" in escalated.json()["error"]["message"]


async def test_delegated_admin_cannot_modify_a_super_admin(client: AsyncClient, fixture_data) -> None:
    super_admin_id, _ = await _create_platform_user("super_admin")
    _, email = await _create_platform_user("platform_admin")
    response = await login(client, email)
    assert response.status_code == 200, response.text
    headers = {"Authorization": f"Bearer {response.json()['tokens']['access_token']}"}

    blocked = await client.patch(
        f"/v1/users/{super_admin_id}",
        headers=headers,
        json={"status": "suspended"},
    )
    assert blocked.status_code == 403, blocked.text
    assert "do not hold" in blocked.json()["error"]["message"]
