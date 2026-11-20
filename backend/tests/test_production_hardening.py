"""Regression tests for the production-hardening pass.

Covers URL validation, PKCE downgrade, password history, current-session
preservation, MFA enrollment at login, invitation delivery status, userinfo
re-resolution and the streaming body cap.
"""

from __future__ import annotations

import uuid

import pyotp
import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import select

from app.core.db import SessionLocal, utcnow
from app.core.middleware import BodySizeLimitMiddleware
from app.modules.access.models import Role, UserRole
from app.modules.applications.auth_service import verify_pkce
from app.modules.applications.schemas import (
    ApplicationClientCreate,
    WebhookCreate,
)
from app.modules.identity.models import User
from tests.conftest import PASSWORD, login
from tests.test_auth_gateway import obtain_code_and_tokens


def test_redirect_uri_rejects_localhost_prefix_hosts() -> None:
    with pytest.raises(ValidationError):
        ApplicationClientCreate(
            name="Bad client",
            redirect_uris=["http://localhost.evil.com/callback"],
        )
    with pytest.raises(ValidationError):
        ApplicationClientCreate(
            name="Bad client",
            redirect_uris=["http://127.0.0.1.attacker.net/callback"],
        )
    accepted = ApplicationClientCreate(
        name="Local client",
        redirect_uris=["http://localhost:3003/callback"],
    )
    assert accepted.redirect_uris == ["http://localhost:3003/callback"]


def test_webhook_rejects_localhost_prefix_hosts() -> None:
    with pytest.raises(ValidationError):
        WebhookCreate(url="http://localhost.evil.com/hook", events=["tenant.created"])
    assert WebhookCreate(url="https://hooks.example.com/basa", events=["tenant.created"])


def test_pkce_rejects_non_ascii_and_oversized_verifiers() -> None:
    assert verify_pkce("café" * 20, "challenge", "S256") is False
    assert verify_pkce("a" * 42, "challenge", "S256") is False
    assert verify_pkce("a" * 129, "challenge", "S256") is False


async def test_public_client_cannot_disable_pkce(
    client: AsyncClient, fixture_data, admin_headers: dict[str, str]
) -> None:
    suffix = uuid.uuid4().hex[:8]
    created = await client.post(
        f"/v1/applications/{fixture_data.application_id}/clients",
        headers=admin_headers,
        json={
            "name": f"Public {suffix}",
            "client_type": "public",
            "redirect_uris": [f"https://public-{suffix}.example.com/callback"],
            "scopes": ["openid", "profile", "email"],
        },
    )
    assert created.status_code == 201, created.text
    client_id = created.json()["client_id"]
    assert created.json()["pkce_required"] is True

    downgraded = await client.patch(
        f"/v1/applications/{fixture_data.application_id}/clients/{client_id}",
        headers=admin_headers,
        json={"pkce_required": False},
    )
    assert downgraded.status_code == 422, downgraded.text


async def test_password_change_keeps_current_session_and_enforces_history(
    client: AsyncClient, fixture_data, admin_headers: dict[str, str]
) -> None:
    email = f"password-{uuid.uuid4().hex[:8]}@example.dev"
    created = await client.post(
        "/v1/users",
        headers=admin_headers,
        json={
            "email": email,
            "full_name": "Password Tester",
            "password": PASSWORD,
            "user_type": "internal",
            "role_keys": [],
        },
    )
    assert created.status_code == 201, created.text

    first = await login(client, email)
    second = await login(client, email)
    assert first.status_code == 200 and second.status_code == 200
    current_headers = {"Authorization": f"Bearer {first.json()['tokens']['access_token']}"}
    other_headers = {"Authorization": f"Bearer {second.json()['tokens']['access_token']}"}

    changed = await client.post(
        "/v1/auth/password/change",
        headers=current_headers,
        json={"current_password": PASSWORD, "new_password": "New!Password#2026"},
    )
    assert changed.status_code == 204, changed.text

    # The session that changed the password stays usable; the sibling is gone.
    assert (await client.get("/v1/auth/me", headers=current_headers)).status_code == 200
    assert (await client.get("/v1/auth/me", headers=other_headers)).status_code == 401

    reuse = await client.post(
        "/v1/auth/password/change",
        headers=current_headers,
        json={"current_password": "New!Password#2026", "new_password": PASSWORD},
    )
    assert reuse.status_code == 422, reuse.text
    assert "last" in reuse.json()["error"]["message"]

    allowed = await client.post(
        "/v1/auth/password/change",
        headers=current_headers,
        json={"current_password": "New!Password#2026", "new_password": "Another!Pass#2026"},
    )
    assert allowed.status_code == 204, allowed.text


async def test_mfa_required_enrolls_at_login(
    client: AsyncClient, fixture_data, admin_headers: dict[str, str]
) -> None:
    email = f"mfa-{uuid.uuid4().hex[:8]}@example.dev"
    created = await client.post(
        "/v1/users",
        headers=admin_headers,
        json={
            "email": email,
            "full_name": "MFA Tester",
            "password": PASSWORD,
            "user_type": "internal",
            "role_keys": [],
        },
    )
    assert created.status_code == 201, created.text

    enabled = await client.put(
        "/v1/access/security-policy",
        headers=admin_headers,
        json={"scope_type": "platform", "settings": {"mfa_required": True}},
    )
    assert enabled.status_code == 200, enabled.text
    try:
        login_response = await login(client, email)
        assert login_response.status_code == 200, login_response.text
        body = login_response.json()
        assert body["status"] == "mfa_enrollment_required"
        challenge = body["challenge_token"]
        assert challenge

        setup = await client.post("/v1/auth/mfa/setup/start", json={"challenge_token": challenge})
        assert setup.status_code == 200, setup.text
        secret = setup.json()["secret"]

        confirm = await client.post(
            "/v1/auth/mfa/setup/confirm",
            json={"challenge_token": challenge, "code": pyotp.TOTP(secret).now()},
        )
        assert confirm.status_code == 200, confirm.text
        confirmed = confirm.json()
        assert confirmed["status"] == "authenticated"
        assert len(confirmed["recovery_codes"]) == 10
        assert confirmed["tokens"]["access_token"]

        me = await client.get(
            "/v1/auth/me",
            headers={"Authorization": f"Bearer {confirmed['tokens']['access_token']}"},
        )
        assert me.status_code == 200
        assert me.json()["user"]["mfa_enabled"] is True
    finally:
        restored = await client.put(
            "/v1/access/security-policy",
            headers=admin_headers,
            json={"scope_type": "platform", "settings": {"mfa_required": False}},
        )
        assert restored.status_code == 200


async def test_invite_reports_email_delivery(
    client: AsyncClient, fixture_data, admin_headers: dict[str, str]
) -> None:
    email = f"invite-status-{uuid.uuid4().hex[:8]}@example.dev"
    created = await client.post(
        f"/v1/tenants/{fixture_data.tenant_a_id}/users",
        headers=admin_headers,
        json={"email": email, "role_key": "tenant_member"},
    )
    assert created.status_code == 201, created.text
    payload = created.json()
    # No SMTP server is configured in the test environment.
    assert payload["invite_email_sent"] is False
    assert payload["invite_token"]


async def test_userinfo_recomputes_roles_after_grant_change(client: AsyncClient, fixture_data) -> None:
    tokens = await obtain_code_and_tokens(client, fixture_data)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    async with SessionLocal() as db:
        user = (
            await db.execute(select(User).where(User.email == fixture_data.tenant_a_user_email))
        ).scalar_one()
        role = (await db.execute(select(Role).where(Role.key == "tenant_owner"))).scalar_one()
        assignment = (
            await db.execute(
                select(UserRole).where(
                    UserRole.user_id == user.id,
                    UserRole.role_id == role.id,
                    UserRole.scope_type == "tenant",
                    UserRole.scope_id == fixture_data.tenant_a_id,
                )
            )
        ).scalar_one()
        await db.delete(assignment)
        await db.commit()

    try:
        stripped = await client.get("/v1/oauth/userinfo", headers=headers)
        assert stripped.status_code == 200, stripped.text
        assert "tenant_owner" not in stripped.json()["roles"]

        async with SessionLocal() as db:
            db.add(
                UserRole(
                    user_id=user.id,
                    role_id=role.id,
                    scope_type="tenant",
                    scope_id=fixture_data.tenant_a_id,
                    created_at=utcnow(),
                )
            )
            await db.commit()

        restored = await client.get("/v1/oauth/userinfo", headers=headers)
        assert restored.status_code == 200, restored.text
        assert "tenant_owner" in restored.json()["roles"]
    finally:
        # Guarantee the session-scoped fixture is intact even if an assertion
        # above failed before the explicit restore.
        async with SessionLocal() as db:
            current = (
                await db.execute(
                    select(UserRole).where(
                        UserRole.user_id == user.id,
                        UserRole.role_id == role.id,
                        UserRole.scope_type == "tenant",
                        UserRole.scope_id == fixture_data.tenant_a_id,
                    )
                )
            ).scalar_one_or_none()
            if current is None:
                db.add(
                    UserRole(
                        user_id=user.id,
                        role_id=role.id,
                        scope_type="tenant",
                        scope_id=fixture_data.tenant_a_id,
                        created_at=utcnow(),
                    )
                )
                await db.commit()


async def test_streaming_body_cap_rejects_chunked_uploads() -> None:
    async def app(scope, receive, send) -> None:
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                break
            if message["type"] == "http.request" and not message.get("more_body"):
                break
        await send(
            {
                "type": "http.response.start",
                "status": 204,
                "headers": [],
            }
        )
        await send({"type": "http.response.body", "body": b""})

    middleware = BodySizeLimitMiddleware(app, max_bytes=4)
    chunks = [
        {"type": "http.request", "body": b"abc", "more_body": True},
        {"type": "http.request", "body": b"def", "more_body": False},
    ]
    sent: list[dict] = []

    async def receive():
        return chunks.pop(0)

    async def send(message):
        sent.append(message)

    await middleware({"type": "http"}, receive, send)
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 413
    assert not any(message.get("status") == 204 for message in sent)


def test_production_settings_reject_demo_defaults(monkeypatch) -> None:
    from cryptography.fernet import Fernet

    from app.core.config import get_settings

    env = {
        "APP_ENV": "production",
        "ENCRYPTION_KEY": Fernet.generate_key().decode(),
        "JWT_PRIVATE_KEY": "configured",
        "JWT_PUBLIC_KEY": "configured",
        "SMTP_HOST": "smtp.example.com",
        "COOKIE_SECURE": "true",
        "DATABASE_URL": "postgresql+asyncpg://app:strong@db:5432/basa",
        "API_BASE_URL": "https://api.example.com",
        "WEB_BASE_URL": "https://console.example.com",
        "JWT_ISSUER": "https://api.example.com",
        "CORS_ORIGINS": "https://console.example.com",
        "ALLOWED_ORIGINS": "https://console.example.com",
        "SEED_DEMO": "false",
        "SEED_ADMIN_PASSWORD": "Adm1n!Basa#2026",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="SEED_ADMIN_PASSWORD"):
        get_settings()

    monkeypatch.setenv("SEED_ADMIN_PASSWORD", "A-Strong-Production-Pass#2026")
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.is_production
    get_settings.cache_clear()


async def test_mfa_enroll_accepts_an_empty_body(client: AsyncClient, admin_headers: dict[str, str]) -> None:
    # The settings dialog posts no JSON body; the endpoint must treat it as an
    # optional request model rather than a 422.
    response = await client.post("/v1/auth/mfa/enroll", headers=admin_headers)
    assert response.status_code == 200, response.text
    assert response.json()["secret"]
