from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import asyncpg
import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
TEST_DB = "basa_test"
PG_HOST = "localhost"
PG_PORT = 5434
PG_OWNER = "platform_owner"
PG_OWNER_PASSWORD = "change-me-owner"
PG_APP = "platform_app"
PG_APP_PASSWORD = "change-me-app"

# Must be set before app.core.config/db are imported anywhere.
os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = f"postgresql+asyncpg://{PG_APP}:{PG_APP_PASSWORD}@{PG_HOST}:{PG_PORT}/{TEST_DB}"
os.environ["MIGRATION_DATABASE_URL"] = (
    f"postgresql+asyncpg://{PG_OWNER}:{PG_OWNER_PASSWORD}@{PG_HOST}:{PG_PORT}/{TEST_DB}"
)
os.environ["KEYS_DIR"] = str(BACKEND_DIR / ".test-keys")
os.environ["REDIS_URL"] = ""
os.environ["COOKIE_SECURE"] = "false"
os.environ["CORS_ORIGINS"] = "http://localhost:3002"
os.environ["ALLOWED_ORIGINS"] = "http://localhost:3002"

from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.core.db import SessionLocal, engine, platform_scope, utcnow  # noqa: E402
from app.core.enums import (  # noqa: E402
    BillingInterval,
    MembershipStatus,
    ScopeType,
    SubscriptionStatus,
    TenantStatus,
    UserStatus,
    UserType,
)
from app.core.security import hash_password, hash_token  # noqa: E402
from app.main import app  # noqa: E402
from app.modules.access.models import Role, UserRole  # noqa: E402
from app.modules.applications.models import ApiCredential, Application, ApplicationClient  # noqa: E402
from app.modules.billing.models import Plan, Subscription  # noqa: E402
from app.modules.identity.models import User  # noqa: E402
from app.modules.organizations.models import Organization  # noqa: E402
from app.modules.tenants.models import Tenant, TenantMembership  # noqa: E402

PASSWORD = "Test!Password#2026"
API_SECRET = "bsk_test_sdk_secret_2026"
OIDC_SECRET = "bsc_test_oidc_secret_2026"
OIDC_REDIRECT = "http://localhost:3003/auth/callback"


@dataclass
class Fixture:
    admin_id: uuid.UUID
    admin_email: str
    organization_id: uuid.UUID
    application_id: uuid.UUID
    application_slug: str
    plan_id: uuid.UUID
    tenant_a_id: uuid.UUID
    tenant_a_slug: str
    tenant_b_id: uuid.UUID
    tenant_b_slug: str
    tenant_a_user_email: str
    tenant_b_user_email: str
    credential_id: uuid.UUID
    oidc_client_id: str
    oidc_client_secret: str
    oidc_redirect_uri: str


async def _recreate_database() -> None:
    admin = await asyncpg.connect(
        host=PG_HOST, port=PG_PORT, user=PG_OWNER, password=PG_OWNER_PASSWORD, database="basa"
    )
    try:
        await admin.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = $1 AND pid <> pg_backend_pid()",
            TEST_DB,
        )
        await admin.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}"')
        await admin.execute(f'CREATE DATABASE "{TEST_DB}" OWNER {PG_OWNER}')
    finally:
        await admin.close()

    conn = await asyncpg.connect(
        host=PG_HOST, port=PG_PORT, user=PG_OWNER, password=PG_OWNER_PASSWORD, database=TEST_DB
    )
    try:
        # The container init scripts only run for POSTGRES_DB, so the throwaway
        # test database needs the same extensions the runtime schema relies on.
        await conn.execute("CREATE EXTENSION IF NOT EXISTS citext")
        await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        await conn.execute(f'GRANT CONNECT ON DATABASE "{TEST_DB}" TO {PG_APP}')
        await conn.execute(f"GRANT USAGE ON SCHEMA public TO {PG_APP}")
        await conn.execute(
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {PG_OWNER} IN SCHEMA public "
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {PG_APP}"
        )
        await conn.execute(
            f"ALTER DEFAULT PRIVILEGES FOR ROLE {PG_OWNER} IN SCHEMA public "
            f"GRANT USAGE, SELECT ON SEQUENCES TO {PG_APP}"
        )
    finally:
        await conn.close()


def _migrate() -> None:
    env = {**os.environ, "PYTHONPATH": str(BACKEND_DIR)}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


async def _seed_fixture() -> Fixture:
    from app.seed import sync_reference

    suffix = uuid.uuid4().hex[:10]

    async with SessionLocal() as db:
        await sync_reference(db)

    async with SessionLocal() as db:
        roles = {
            role.key: role
            for role in (await db.execute(select(Role).where(Role.key.in_(["super_admin", "tenant_owner"]))))
            .scalars()
            .all()
        }

        admin = User(
            email=f"admin-{suffix}@basa.dev",
            full_name="Test Administrator",
            user_type=UserType.INTERNAL.value,
            status=UserStatus.ACTIVE.value,
            password_hash=hash_password(PASSWORD),
            email_verified_at=None,
        )
        db.add(admin)
        await db.flush()
        db.add(
            UserRole(
                user_id=admin.id,
                role_id=roles["super_admin"].id,
                scope_type=ScopeType.PLATFORM.value,
                scope_id=None,
                created_at=utcnow(),
            )
        )

        org = Organization(
            name=f"Test Organization {suffix}",
            slug=f"test-org-{suffix}",
            status="active",
        )
        db.add(org)
        await db.flush()

        application = Application(
            organization_id=org.id,
            name=f"Test Application {suffix}",
            slug=f"test-app-{suffix}",
            status="active",
            owner_id=admin.id,
            auth_config={},
            integration_config={},
            subscription_config={},
            resource_allocation={},
            metadata_json={},
        )
        db.add(application)
        await db.flush()

        plan = Plan(
            application_id=None,
            key=f"test-plan-{suffix}",
            name="Test Plan",
            limits={"api_calls": 1000, "storage_gb": 10, "seats": 3},
            features=["test"],
        )
        db.add(plan)
        await db.flush()

        def make_user(local: str) -> User:
            user = User(
                email=f"{local}-{suffix}@example.dev",
                full_name=local.title(),
                user_type=UserType.EXTERNAL.value,
                status=UserStatus.ACTIVE.value,
                password_hash=hash_password(PASSWORD),
            )
            db.add(user)
            return user

        user_a = make_user("alpha")
        user_b = make_user("beta")
        await db.flush()

        async with platform_scope(db):
            tenant_a = Tenant(
                application_id=application.id,
                organization_id=org.id,
                name="Alpha Tenant",
                slug=f"alpha-{suffix}",
                status=TenantStatus.ACTIVE.value,
                owner_id=user_a.id,
                isolation_strategy="shared_database",
                quotas={"api_calls": 1000, "storage_gb": 10, "seats": 3},
                configuration={},
                branding={},
                metadata_json={},
            )
            tenant_b = Tenant(
                application_id=application.id,
                organization_id=org.id,
                name="Beta Tenant",
                slug=f"beta-{suffix}",
                status=TenantStatus.ACTIVE.value,
                owner_id=user_b.id,
                isolation_strategy="shared_database",
                quotas={"api_calls": 1000, "storage_gb": 10, "seats": 3},
                configuration={},
                branding={},
                metadata_json={},
            )
            db.add_all([tenant_a, tenant_b])
            await db.flush()

            db.add_all(
                [
                    Subscription(
                        tenant_id=tenant_a.id,
                        plan_id=plan.id,
                        status=SubscriptionStatus.ACTIVE.value,
                        billing_interval=BillingInterval.MONTHLY.value,
                        current_period_start=utcnow(),
                        current_period_end=utcnow(),
                    ),
                    Subscription(
                        tenant_id=tenant_b.id,
                        plan_id=plan.id,
                        status=SubscriptionStatus.ACTIVE.value,
                        billing_interval=BillingInterval.MONTHLY.value,
                        current_period_start=utcnow(),
                        current_period_end=utcnow(),
                    ),
                ]
            )
            db.add_all(
                [
                    TenantMembership(
                        tenant_id=tenant_a.id,
                        user_id=user_a.id,
                        status=MembershipStatus.ACTIVE.value,
                        is_billing_contact=True,
                        created_at=utcnow(),
                    ),
                    TenantMembership(
                        tenant_id=tenant_b.id,
                        user_id=user_b.id,
                        status=MembershipStatus.ACTIVE.value,
                        is_billing_contact=True,
                        created_at=utcnow(),
                    ),
                ]
            )
            db.add_all(
                [
                    UserRole(
                        user_id=user_a.id,
                        role_id=roles["tenant_owner"].id,
                        scope_type=ScopeType.TENANT.value,
                        scope_id=tenant_a.id,
                        created_at=utcnow(),
                    ),
                    UserRole(
                        user_id=user_b.id,
                        role_id=roles["tenant_owner"].id,
                        scope_type=ScopeType.TENANT.value,
                        scope_id=tenant_b.id,
                        created_at=utcnow(),
                    ),
                ]
            )
            await db.commit()

        credential = ApiCredential(
            application_id=application.id,
            name="Test credential",
            key_prefix=API_SECRET[:12],
            secret_hash=hash_token(API_SECRET),
            scopes=[
                "app:config.read",
                "app:usage.write",
                "app:audit.write",
                "app:tenant.resolve",
                "app:authorization.check",
                "app:identity.read",
            ],
            status="active",
            created_by=admin.id,
            created_at=utcnow(),
        )
        db.add(credential)
        await db.commit()

        oidc_client_id = f"basa_test_{suffix}"
        oidc_client = ApplicationClient(
            application_id=application.id,
            name="Test web client",
            client_id=oidc_client_id,
            client_secret_hash=hash_token(OIDC_SECRET),
            client_type="confidential",
            redirect_uris=[OIDC_REDIRECT],
            post_logout_redirect_uris=["http://localhost:3003/"],
            allowed_origins=["http://localhost:3003"],
            scopes=["openid", "profile", "email", "offline_access"],
            pkce_required=True,
            status="active",
            created_by=admin.id,
        )
        db.add(oidc_client)
        await db.commit()

        fixture = Fixture(
            admin_id=admin.id,
            admin_email=admin.email,
            organization_id=org.id,
            application_id=application.id,
            application_slug=application.slug,
            plan_id=plan.id,
            tenant_a_id=tenant_a.id,
            tenant_a_slug=tenant_a.slug,
            tenant_b_id=tenant_b.id,
            tenant_b_slug=tenant_b.slug,
            tenant_a_user_email=user_a.email,
            tenant_b_user_email=user_b.email,
            credential_id=credential.id,
            oidc_client_id=oidc_client_id,
            oidc_client_secret=OIDC_SECRET,
            oidc_redirect_uri=OIDC_REDIRECT,
        )

    # The seeding loop is a separate asyncio.run() loop. Drop every pooled
    # connection created there so tests do not pick up a connection bound to a
    # closed event loop.
    await engine.dispose()
    return fixture


@pytest.fixture(scope="session")
def fixture_data() -> Fixture:
    asyncio.run(_recreate_database())
    _migrate()
    return asyncio.run(_seed_fixture())


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


async def login(client: AsyncClient, email: str, password: str = PASSWORD):
    return await client.post("/v1/auth/login", json={"email": email, "password": password})


@pytest.fixture(autouse=True)
def reset_rate_limits() -> None:
    """Keep the in-process limiter from leaking between tests (Redis is disabled)."""
    from app.core import rate_limit

    rate_limit._local.clear()


@pytest.fixture
async def admin_headers(client: AsyncClient, fixture_data: Fixture) -> dict[str, str]:
    response = await login(client, fixture_data.admin_email)
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['tokens']['access_token']}"}


@pytest.fixture
async def tenant_a_headers(client: AsyncClient, fixture_data: Fixture) -> dict[str, str]:
    response = await login(client, fixture_data.tenant_a_user_email)
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['tokens']['access_token']}"}


@pytest.fixture
async def tenant_b_headers(client: AsyncClient, fixture_data: Fixture) -> dict[str, str]:
    response = await login(client, fixture_data.tenant_b_user_email)
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['tokens']['access_token']}"}


@pytest.fixture
async def service_headers(client: AsyncClient, fixture_data: Fixture) -> dict[str, str]:
    response = await client.post(
        "/v1/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": str(fixture_data.credential_id),
            "client_secret": API_SECRET,
        },
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}
