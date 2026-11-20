from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import DBAPIError

from app.core.db import SessionLocal, platform_scope, tenant_scope, utcnow
from app.modules.audit.models import AuditLog
from app.modules.tenants.models import Tenant


async def test_database_rls_denies_without_tenant_context(fixture_data) -> None:
    async with SessionLocal() as db:
        count = (await db.execute(select(func.count()).select_from(Tenant))).scalar_one()
    assert count == 0


async def test_database_rls_narrows_to_tenant_context(fixture_data) -> None:
    async with SessionLocal() as db:
        async with tenant_scope(db, fixture_data.tenant_a_id):
            rows = (await db.execute(select(Tenant.id))).scalars().all()
    assert rows == [fixture_data.tenant_a_id]


async def test_platform_scope_can_read_all_tenants(fixture_data) -> None:
    async with SessionLocal() as db:
        async with platform_scope(db):
            ids = set((await db.execute(select(Tenant.id))).scalars().all())
    assert {fixture_data.tenant_a_id, fixture_data.tenant_b_id}.issubset(ids)


async def test_cross_tenant_reads_are_denied(
    client: AsyncClient, fixture_data, tenant_a_headers: dict[str, str]
) -> None:
    own = await client.get(f"/v1/tenants/{fixture_data.tenant_a_id}", headers=tenant_a_headers)
    assert own.status_code == 200
    assert own.json()["name"] == "Alpha Tenant"

    other = await client.get(f"/v1/tenants/{fixture_data.tenant_b_id}", headers=tenant_a_headers)
    assert other.status_code == 403

    other_users = await client.get(f"/v1/tenants/{fixture_data.tenant_b_id}/users", headers=tenant_a_headers)
    assert other_users.status_code == 403


async def test_tenant_audit_view_only_returns_own_tenant(
    client: AsyncClient, fixture_data, tenant_a_headers: dict[str, str]
) -> None:
    async with SessionLocal() as db:
        async with platform_scope(db):
            db.add_all(
                [
                    AuditLog(
                        action="test.alpha_event",
                        resource_type="test",
                        resource_id="alpha",
                        scope_type="tenant",
                        scope_id=fixture_data.tenant_a_id,
                        outcome="success",
                        actor_email="alpha@example.dev",
                        created_at=utcnow(),
                    ),
                    AuditLog(
                        action="test.beta_event",
                        resource_type="test",
                        resource_id="beta",
                        scope_type="tenant",
                        scope_id=fixture_data.tenant_b_id,
                        outcome="success",
                        actor_email="beta@example.dev",
                        created_at=utcnow(),
                    ),
                ]
            )
            await db.commit()

    response = await client.get(f"/v1/tenants/{fixture_data.tenant_a_id}/audit", headers=tenant_a_headers)
    assert response.status_code == 200
    actions = {item["action"] for item in response.json()["items"]}
    assert "test.alpha_event" in actions
    assert "test.beta_event" not in actions


async def test_audit_log_is_append_only(fixture_data) -> None:
    async with SessionLocal() as db:
        async with platform_scope(db):
            entry = AuditLog(
                action="test.append_only",
                resource_type="test",
                resource_id="append-only",
                scope_type="platform",
                outcome="success",
                actor_email="tester@example.dev",
                created_at=utcnow(),
            )
            db.add(entry)
            await db.commit()
            log_id = entry.id

            with pytest.raises(DBAPIError):
                await db.execute(update(AuditLog).where(AuditLog.id == log_id).values(outcome="failure"))
            await db.rollback()

            with pytest.raises(DBAPIError):
                await db.execute(delete(AuditLog).where(AuditLog.id == log_id))
            await db.rollback()

            still_there = await db.get(AuditLog, log_id)
            assert still_there is not None
            assert still_there.outcome == "success"
