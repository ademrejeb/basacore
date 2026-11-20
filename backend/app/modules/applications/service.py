from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import utcnow
from app.core.enums import EnvironmentKind, TenantStatus
from app.core.security import generate_token, hash_token, secret_box
from app.modules.applications.models import (
    ApiCredential,
    Application,
    ApplicationConfiguration,
    ApplicationEnvironment,
    FeatureFlag,
    FeatureFlagOverride,
)
from app.modules.tenants.models import Tenant


async def application_stats(
    db: AsyncSession, application_ids: list[uuid.UUID]
) -> dict[uuid.UUID, dict[str, Any]]:
    if not application_ids:
        return {}
    tenant_rows = (
        await db.execute(
            select(Tenant.application_id, Tenant.status, func.count(Tenant.id))
            .where(Tenant.application_id.in_(application_ids))
            .group_by(Tenant.application_id, Tenant.status)
        )
    ).all()
    stats: dict[uuid.UUID, dict[str, Any]] = {
        app_id: {"tenants": 0, "active": 0, "trial": 0, "suspended": 0} for app_id in application_ids
    }
    for app_id, status_value, count in tenant_rows:
        entry = stats[app_id]
        entry["tenants"] += count
        if status_value in (TenantStatus.ACTIVE.value, TenantStatus.PROVISIONING.value):
            entry["active"] += count
        elif status_value == TenantStatus.TRIAL.value:
            entry["trial"] += count
        elif status_value == TenantStatus.SUSPENDED.value:
            entry["suspended"] += count
    return stats


async def latest_config(
    db: AsyncSession, application_id: uuid.UUID, environment_id: uuid.UUID | None
) -> ApplicationConfiguration | None:
    stmt = (
        select(ApplicationConfiguration)
        .where(
            ApplicationConfiguration.application_id == application_id,
            ApplicationConfiguration.environment_id == environment_id
            if environment_id
            else ApplicationConfiguration.environment_id.is_(None),
        )
        .order_by(ApplicationConfiguration.version.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def publish_config(
    db: AsyncSession,
    *,
    application_id: uuid.UUID,
    environment_id: uuid.UUID | None,
    config: dict[str, Any],
    note: str | None,
    created_by: uuid.UUID | None,
) -> ApplicationConfiguration:
    existing = await latest_config(db, application_id, environment_id)
    version = (existing.version + 1) if existing else 1
    record = ApplicationConfiguration(
        application_id=application_id,
        environment_id=environment_id,
        version=version,
        config=config,
        note=note,
        created_by=created_by,
        created_at=utcnow(),
    )
    db.add(record)
    return record


async def issue_credential(
    db: AsyncSession,
    *,
    application: Application,
    name: str,
    scopes: list[str],
    created_by: uuid.UUID,
    expires_in_days: int | None,
) -> tuple[ApiCredential, str]:
    plaintext = f"bsk_{application.slug}_{generate_token(31)}"
    credential = ApiCredential(
        application_id=application.id,
        name=name,
        key_prefix=plaintext[:18],
        secret_hash=hash_token(plaintext),
        scopes=scopes,
        status="active",
        created_by=created_by,
        created_at=utcnow(),
        expires_at=(utcnow() + timedelta(days=expires_in_days)) if expires_in_days else None,
    )
    db.add(credential)
    return credential, plaintext


def flag_enabled(flag: FeatureFlag, tenant: Tenant | None, override: bool | None = None) -> bool:
    if override is not None:
        return override
    if tenant is None:
        return flag.enabled
    if not flag.enabled:
        return False
    bucket = int(hashlib.sha256(f"{flag.id}:{tenant.id}".encode()).hexdigest(), 16) % 100
    return bucket < flag.rollout_percentage


async def build_context(
    db: AsyncSession, tenant: Tenant, environment_id: uuid.UUID | None = None
) -> dict[str, Any]:
    """Effective configuration for one tenant, including flags and plan limits."""
    from app.modules.billing.models import Plan, Subscription, UsageRecord

    app_config = await latest_config(db, tenant.application_id, environment_id)

    from app.modules.tenants.models import TenantConfiguration

    tenant_row = (
        await db.execute(
            select(TenantConfiguration)
            .where(TenantConfiguration.tenant_id == tenant.id)
            .order_by(TenantConfiguration.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    flags = (
        (await db.execute(select(FeatureFlag).where(FeatureFlag.application_id == tenant.application_id)))
        .scalars()
        .all()
    )
    overrides = (
        (
            await db.execute(
                select(FeatureFlagOverride).where(
                    FeatureFlagOverride.tenant_id == tenant.id,
                    FeatureFlagOverride.flag_id.in_([f.id for f in flags] or [uuid.uuid4()]),
                )
            )
        )
        .scalars()
        .all()
    )
    override_map = {o.flag_id: o.enabled for o in overrides}

    subscription = (
        await db.execute(select(Subscription).where(Subscription.tenant_id == tenant.id))
    ).scalar_one_or_none()
    plan = await db.get(Plan, subscription.plan_id) if subscription else None
    # Quotas are per billing period, so the context must report the same window
    # the enforcer uses. Summing all history made every tenant look over quota
    # after its first month.
    from app.modules.billing.service import period_start

    usage = (
        await db.execute(
            select(UsageRecord.metric, func.sum(UsageRecord.quantity))
            .where(
                UsageRecord.tenant_id == tenant.id,
                UsageRecord.period_start == period_start(),
            )
            .group_by(UsageRecord.metric)
        )
    ).all()

    merged: dict[str, Any] = {}
    if app_config:
        merged.update(app_config.config)
    merged.update(tenant.configuration or {})
    if tenant_row:
        merged.update(tenant_row.config or {})

    return {
        "tenant": {
            "id": tenant.id,
            "name": tenant.name,
            "slug": tenant.slug,
            "status": tenant.status,
            "isolation_strategy": tenant.isolation_strategy,
            "branding": tenant.branding,
            "quotas": tenant.quotas,
        },
        "application_id": tenant.application_id,
        "config": merged,
        "features": {f.key: flag_enabled(f, tenant, override_map.get(f.id)) for f in flags},
        "plan": (
            {
                "id": plan.id,
                "key": plan.key,
                "name": plan.name,
                "limits": plan.limits,
                "features": plan.features,
            }
            if plan
            else None
        ),
        "subscription": (
            {
                "id": subscription.id,
                "status": subscription.status,
                "current_period_end": subscription.current_period_end,
                "cancel_at_period_end": subscription.cancel_at_period_end,
            }
            if subscription
            else None
        ),
        "usage": {metric: float(total) for metric, total in usage},
        "config_version": app_config.version if app_config else 0,
        "generated_at": datetime.now(UTC),
    }


def default_environment(application_id: uuid.UUID) -> ApplicationEnvironment:
    return ApplicationEnvironment(
        application_id=application_id,
        name="Production",
        slug="production",
        kind=EnvironmentKind.PRODUCTION.value,
        status="pending",
        is_default=True,
    )


def application_public(application: Application) -> dict[str, Any]:
    return {
        "id": application.id,
        "organization_id": application.organization_id,
        "name": application.name,
        "slug": application.slug,
        "description": application.description,
        "status": application.status,
        "owner_id": application.owner_id,
        "base_url": application.base_url,
        "isolation_strategy": application.isolation_strategy,
        "auth_config": application.auth_config,
        "integration_config": application.integration_config,
        "subscription_config": application.subscription_config,
        "resource_allocation": application.resource_allocation,
        "created_at": application.created_at,
        "updated_at": application.updated_at,
        "archived_at": application.archived_at,
    }


def encrypt_webhook_secret(secret: str) -> str:
    return secret_box().encrypt(secret)


def generate_webhook_token() -> str:
    return generate_token(32)
