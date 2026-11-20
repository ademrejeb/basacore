"""Seed reference data and a realistic local demo dataset.

Run with ``python -m app.seed``. Safe to re-run: records are matched by natural
keys and only missing rows are created. The seed is intended for development and
staging; production deployments should run ``python -m app.seed --reference``.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import random
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import SessionLocal, platform_scope, utcnow
from app.core.enums import (
    ApplicationStatus,
    BillingInterval,
    DomainStatus,
    EnvironmentKind,
    EnvironmentStatus,
    IncidentSeverity,
    IncidentStatus,
    InvoiceStatus,
    MembershipStatus,
    ProjectStatus,
    ResourceStatus,
    ResourceType,
    ScopeType,
    SubscriptionStatus,
    TaskPriority,
    TaskStatus,
    TenantStatus,
    UserStatus,
    UserType,
)
from app.core.permissions import PERMISSIONS, ROLES, permissions_for_role_key
from app.core.security import hash_password, hash_token, secret_box
from app.modules.access.models import Permission, Role, RolePermission, SecurityPolicy, UserRole
from app.modules.access.policy import DEFAULT_POLICY
from app.modules.applications.models import (
    ApiCredential,
    Application,
    ApplicationClient,
    ApplicationConfiguration,
    ApplicationEnvironment,
    FeatureFlag,
    FeatureFlagOverride,
    Integration,
    Webhook,
)
from app.modules.audit.models import AuditLog
from app.modules.billing.models import Invoice, Payment, Plan, Subscription, UsageRecord
from app.modules.billing.service import period_start
from app.modules.identity.models import User
from app.modules.infrastructure.models import (
    Deployment,
    Incident,
    InfrastructureResource,
    MonitoringAlert,
)
from app.modules.organizations.models import Organization
from app.modules.projects.models import Notification, Project, Task, TaskComment
from app.modules.tenants.models import (
    Tenant,
    TenantConfiguration,
    TenantDomain,
    TenantMembership,
)

settings = get_settings()
rng = random.Random(20261102)


def ago(days: int = 0, hours: int = 0) -> datetime:
    return utcnow() - timedelta(days=days, hours=hours)


async def one(db: AsyncSession, model, *criteria):
    return (await db.execute(select(model).where(*criteria))).scalar_one_or_none()


async def ensure_user(
    db: AsyncSession,
    *,
    email: str,
    full_name: str,
    user_type: UserType,
    password: str | None = None,
    job_title: str | None = None,
    department: str | None = None,
    status: UserStatus = UserStatus.ACTIVE,
) -> User:
    user = await one(db, User, User.email == email.lower())
    if user is None:
        user = User(
            email=email.lower(),
            full_name=full_name,
            user_type=user_type.value,
            status=status.value,
            password_hash=hash_password(password) if password else None,
            job_title=job_title,
            department=department,
            email_verified_at=utcnow() if password else None,
            password_changed_at=utcnow() if password else None,
        )
        db.add(user)
        await db.flush()
    return user


async def assign(
    db: AsyncSession,
    *,
    user: User,
    role_key: str,
    scope_type: ScopeType,
    scope_id: uuid.UUID | None,
    granted_by: uuid.UUID | None = None,
) -> None:
    role = await one(db, Role, Role.key == role_key)
    if role is None:
        raise RuntimeError(f"seed ordering error: role {role_key} missing")
    existing = await one(
        db,
        UserRole,
        UserRole.user_id == user.id,
        UserRole.role_id == role.id,
        UserRole.scope_type == scope_type.value,
        UserRole.scope_id == scope_id if scope_id else UserRole.scope_id.is_(None),
    )
    if existing is None:
        db.add(
            UserRole(
                user_id=user.id,
                role_id=role.id,
                scope_type=scope_type.value,
                scope_id=scope_id,
                granted_by=granted_by,
                created_at=ago(rng.randint(1, 120)),
            )
        )


async def sync_reference(db: AsyncSession) -> None:
    for spec in PERMISSIONS:
        permission = await one(db, Permission, Permission.key == spec.key)
        if permission is None:
            db.add(Permission(key=spec.key, description=spec.description, category=spec.category))
        else:
            permission.description = spec.description
            permission.category = spec.category
    await db.flush()

    for spec in ROLES:
        role = await one(db, Role, Role.key == spec.key)
        if role is None:
            role = Role(
                key=spec.key,
                name=spec.name,
                description=spec.description,
                scope_type=spec.scope_type.value,
                is_system=True,
                organization_id=None,
            )
            db.add(role)
            await db.flush()
        else:
            role.name = spec.name
            role.description = spec.description
            role.scope_type = spec.scope_type.value
            role.is_system = True
        desired = permissions_for_role_key(spec.key)
        current = set(
            (await db.execute(select(RolePermission.permission_key).where(RolePermission.role_id == role.id)))
            .scalars()
            .all()
        )
        for key in desired - current:
            db.add(RolePermission(role_id=role.id, permission_key=key))
        for key in current - desired:
            await db.execute(
                RolePermission.__table__.delete().where(
                    RolePermission.role_id == role.id, RolePermission.permission_key == key
                )
            )

    default_policy = await one(db, SecurityPolicy, SecurityPolicy.is_default.is_(True))
    if default_policy is None:
        db.add(
            SecurityPolicy(
                name="Platform default",
                scope_type=ScopeType.PLATFORM.value,
                scope_id=None,
                settings=dict(DEFAULT_POLICY),
                is_default=True,
            )
        )
    await db.commit()


async def seed_demo(db: AsyncSession) -> None:
    password = settings.seed_admin_password
    admin = await ensure_user(
        db,
        email=settings.seed_admin_email,
        full_name="Avery Basa",
        user_type=UserType.INTERNAL,
        password=password,
        job_title="Chief Platform Officer",
        department="Platform",
    )
    await assign(db, user=admin, role_key="super_admin", scope_type=ScopeType.PLATFORM, scope_id=None)

    team = [
        ("maya.chen@basa.dev", "Maya Chen", "platform_admin", "Platform Engineering", "Director of Platform"),
        ("omar.haddad@basa.dev", "Omar Haddad", "security_admin", "Security", "Head of Security"),
        ("lena.ortiz@basa.dev", "Lena Ortiz", "finance_admin", "Finance", "Finance Lead"),
        ("sam.okafor@basa.dev", "Sam Okafor", "support_agent", "Customer Success", "Support Lead"),
        ("priya.nair@basa.dev", "Priya Nair", "infrastructure_admin", "Infrastructure", "SRE Lead"),
        ("tom.becker@basa.dev", "Tom Becker", "auditor", "Compliance", "Internal Auditor"),
    ]
    internal: dict[str, User] = {}
    for email, name, role_key, department, title in team:
        user = await ensure_user(
            db,
            email=email,
            full_name=name,
            user_type=UserType.INTERNAL,
            password=password,
            job_title=title,
            department=department,
        )
        await assign(db, user=user, role_key=role_key, scope_type=ScopeType.PLATFORM, scope_id=None)
        internal[email] = user

    org_specs = [
        (
            "Northwind Labs",
            "northwind-labs",
            "Product studio building developer tooling.",
            "billing@northwind.dev",
        ),
        ("Acme Cloud", "acme-cloud", "Cloud infrastructure and managed services.", "finance@acme.dev"),
        ("Vertex Systems", "vertex-systems", "Enterprise data and analytics platforms.", "ap@vertex.dev"),
        (
            "Lumen Health",
            "lumen-health",
            "Digital patient engagement for health systems.",
            "billing@lumen.dev",
        ),
        ("Orbital Retail", "orbital-retail", "Omnichannel commerce and fulfilment.", "accounts@orbital.dev"),
    ]
    orgs: dict[str, Organization] = {}
    for name, slug, description, billing_email in org_specs:
        org = await one(db, Organization, Organization.slug == slug)
        if org is None:
            org = Organization(
                name=name,
                slug=slug,
                description=description,
                billing_email=billing_email,
                status="active",
                created_at=ago(rng.randint(90, 400)),
            )
            db.add(org)
            await db.flush()
        orgs[slug] = org

    app_specs = [
        (
            "Atlas CRM",
            "atlas-crm",
            "northwind-labs",
            "Customer relationship management for growing teams.",
            "https://atlas.example.com",
            ApplicationStatus.ACTIVE,
        ),
        (
            "Nova Billing",
            "nova-billing",
            "northwind-labs",
            "Usage-based billing and revenue operations.",
            "https://nova.example.com",
            ApplicationStatus.ACTIVE,
        ),
        (
            "Helix Identity",
            "helix-identity",
            "acme-cloud",
            "Workforce identity and access management.",
            "https://helix.example.com",
            ApplicationStatus.ACTIVE,
        ),
        (
            "Lumen Care",
            "lumen-care",
            "lumen-health",
            "Patient messaging, scheduling and care plans.",
            "https://care.example.com",
            ApplicationStatus.ACTIVE,
        ),
        (
            "Orbital Commerce",
            "orbital-commerce",
            "orbital-retail",
            "Composable commerce platform.",
            "https://orbital.example.com",
            ApplicationStatus.DRAFT,
        ),
        (
            "Vertex Analytics",
            "vertex-analytics",
            "vertex-systems",
            "Warehouse-native analytics workspace.",
            "https://analytics.example.com",
            ApplicationStatus.ACTIVE,
        ),
    ]
    apps: dict[str, Application] = {}
    envs: dict[tuple[str, str], ApplicationEnvironment] = {}
    for index, (name, slug, org_slug, description, base_url, status) in enumerate(app_specs):
        app = await one(db, Application, Application.slug == slug)
        if app is None:
            app = Application(
                organization_id=orgs[org_slug].id,
                name=name,
                slug=slug,
                description=description,
                status=status.value,
                owner_id=admin.id if index % 2 == 0 else internal["maya.chen@basa.dev"].id,
                base_url=base_url,
                auth_config={"session_ttl_minutes": 60, "mfa": "optional"},
                integration_config={"analytics": "enabled"},
                resource_allocation={"tier": "shared", "region": "eu-west-1"},
                created_at=ago(rng.randint(30, 350)),
            )
            db.add(app)
            await db.flush()
        apps[slug] = app
        for env_slug, env_name, kind, env_status in (
            ("production", "Production", EnvironmentKind.PRODUCTION, EnvironmentStatus.ACTIVE),
            ("staging", "Staging", EnvironmentKind.STAGING, EnvironmentStatus.ACTIVE),
        ):
            env = await one(
                db,
                ApplicationEnvironment,
                ApplicationEnvironment.application_id == app.id,
                ApplicationEnvironment.slug == env_slug,
            )
            if env is None:
                env = ApplicationEnvironment(
                    application_id=app.id,
                    name=env_name,
                    slug=env_slug,
                    kind=kind.value,
                    base_url=base_url
                    if env_slug == "production"
                    else base_url.replace("https://", "https://staging."),
                    api_base_url=(
                        base_url
                        if env_slug == "production"
                        else base_url.replace("https://", "https://staging.")
                    )
                    + "/api",
                    status=env_status.value,
                    is_default=env_slug == "production",
                    config={"log_level": "info"},
                    last_health_check_at=ago(0, rng.randint(1, 30)),
                    health_latency_ms=rng.randint(40, 180),
                )
                db.add(env)
                await db.flush()
            envs[(slug, env_slug)] = env

    plan_specs = [
        (
            "starter",
            "Starter",
            0,
            0,
            {"api_calls": 100_000, "storage_gb": 5, "seats": 5},
            ["community_support"],
        ),
        (
            "growth",
            "Growth",
            9_900,
            99_000,
            {"api_calls": 1_000_000, "storage_gb": 100, "seats": 25},
            ["email_support", "sso"],
        ),
        (
            "scale",
            "Scale",
            29_900,
            299_000,
            {"api_calls": 5_000_000, "storage_gb": 500, "seats": 100},
            ["priority_support", "sso", "audit_export"],
        ),
        (
            "enterprise",
            "Enterprise",
            99_900,
            999_000,
            {"api_calls": 25_000_000, "storage_gb": 2_000, "seats": 500},
            ["dedicated_support", "sso", "audit_export", "custom_dpa"],
        ),
    ]
    plans: dict[str, Plan] = {}
    for order, (key, name, monthly, yearly, limits, features) in enumerate(plan_specs):
        plan = await one(db, Plan, Plan.key == key, Plan.application_id.is_(None))
        if plan is None:
            plan = Plan(
                application_id=None,
                key=key,
                name=name,
                description=f"{name} plan for Basacore applications.",
                price_monthly_cents=monthly,
                price_yearly_cents=yearly,
                currency="USD",
                limits=limits,
                features=features,
                is_public=True,
                sort_order=order,
                created_at=ago(300),
            )
            db.add(plan)
            await db.flush()
        plans[key] = plan

    tenant_specs = [
        (
            "Atlas CRM",
            "Northwind Labs",
            "Northwind Sales",
            "northwind-sales",
            TenantStatus.ACTIVE,
            "growth",
            BillingInterval.MONTHLY,
            0,
        ),
        (
            "Atlas CRM",
            "Northwind Labs",
            "Brightline Media",
            "brightline-media",
            TenantStatus.TRIAL,
            "scale",
            BillingInterval.MONTHLY,
            9,
        ),
        (
            "Atlas CRM",
            "Acme Cloud",
            "Kestrel Logistics",
            "kestrel-logistics",
            TenantStatus.SUSPENDED,
            "starter",
            BillingInterval.MONTHLY,
            120,
        ),
        (
            "Nova Billing",
            "Northwind Labs",
            "Acme Cloud",
            "acme-cloud",
            TenantStatus.ACTIVE,
            "scale",
            BillingInterval.YEARLY,
            0,
        ),
        (
            "Nova Billing",
            "Vertex Systems",
            "Vertex Systems",
            "vertex-systems",
            TenantStatus.TRIAL,
            "growth",
            BillingInterval.MONTHLY,
            5,
        ),
        (
            "Helix Identity",
            "Acme Cloud",
            "Acme Workforce",
            "acme-workforce",
            TenantStatus.ACTIVE,
            "enterprise",
            BillingInterval.YEARLY,
            0,
        ),
        (
            "Helix Identity",
            "Lumen Health",
            "Lumen Health",
            "lumen-health",
            TenantStatus.ACTIVE,
            "growth",
            BillingInterval.MONTHLY,
            0,
        ),
        (
            "Helix Identity",
            "Orbital Retail",
            "Orbital Retail",
            "orbital-retail",
            TenantStatus.PROVISIONING,
            "starter",
            BillingInterval.MONTHLY,
            14,
        ),
        (
            "Lumen Care",
            "Lumen Health",
            "Lumen Clinics",
            "lumen-clinics",
            TenantStatus.ACTIVE,
            "scale",
            BillingInterval.MONTHLY,
            0,
        ),
        (
            "Lumen Care",
            "Vertex Systems",
            "Lumen Partners",
            "lumen-partners",
            TenantStatus.TRIAL,
            "starter",
            BillingInterval.MONTHLY,
            7,
        ),
        (
            "Orbital Commerce",
            "Orbital Retail",
            "Orbital Storefront",
            "orbital-storefront",
            TenantStatus.TRIAL,
            "growth",
            BillingInterval.MONTHLY,
            3,
        ),
        (
            "Vertex Analytics",
            "Vertex Systems",
            "Vertex Insights",
            "vertex-insights",
            TenantStatus.ACTIVE,
            "enterprise",
            BillingInterval.YEARLY,
            0,
        ),
        (
            "Vertex Analytics",
            "Northwind Labs",
            "Northwind Data",
            "northwind-data",
            TenantStatus.ACTIVE,
            "growth",
            BillingInterval.MONTHLY,
            0,
        ),
    ]
    tenant_user_specs = [
        ("emma.wilson@northwind.dev", "Emma Wilson", "northwind-sales", "tenant_owner"),
        ("liam.patel@acme.dev", "Liam Patel", "acme-cloud", "tenant_owner"),
        ("sofia.rossi@vertex.dev", "Sofia Rossi", "vertex-insights", "tenant_owner"),
        ("noah.kim@lumen.dev", "Noah Kim", "lumen-clinics", "tenant_owner"),
        ("ava.nguyen@brightline.dev", "Ava Nguyen", "brightline-media", "tenant_admin"),
        ("marcus.brown@kestrel.dev", "Marcus Brown", "kestrel-logistics", "tenant_admin"),
    ]
    tenant_users: dict[str, User] = {}
    for email, name, _, _ in tenant_user_specs:
        tenant_users[email] = await ensure_user(
            db,
            email=email,
            full_name=name,
            user_type=UserType.EXTERNAL,
            password=password,
            job_title="Operations Lead",
        )

    tenants: dict[str, Tenant] = {}
    for app_name, org_name, name, slug, status, plan_key, interval, trial_days in tenant_specs:
        app = apps[next(slug_key for slug_key, app_obj in apps.items() if app_obj.name == app_name)]
        org = orgs[next(slug_key for slug_key, org_obj in orgs.items() if org_obj.name == org_name)]
        plan = plans[plan_key]
        tenant = await one(db, Tenant, Tenant.application_id == app.id, Tenant.slug == slug)
        owner_spec = next((spec for spec in tenant_user_specs if spec[2] == slug), None)
        owner = tenant_users[owner_spec[0]] if owner_spec else None
        if tenant is None:
            tenant = Tenant(
                application_id=app.id,
                organization_id=org.id,
                name=name,
                slug=slug,
                status=status.value,
                owner_id=owner.id if owner else None,
                isolation_strategy="shared_database",
                quotas={k: v for k, v in plan.limits.items() if isinstance(v, int)},
                configuration={
                    "locale": "en-US",
                    "support_tier": plan_key,
                    "onboarding_complete": status != TenantStatus.PROVISIONING,
                },
                branding={"primary_color": "#4f46e5", "logo_url": None},
                trial_ends_at=utcnow() + timedelta(days=trial_days) if trial_days else None,
                suspended_at=ago(20) if status == TenantStatus.SUSPENDED else None,
                created_at=ago(rng.randint(2, 170)),
            )
            db.add(tenant)
            await db.flush()
        tenants[slug] = tenant

        if owner:
            membership = await one(
                db,
                TenantMembership,
                TenantMembership.tenant_id == tenant.id,
                TenantMembership.user_id == owner.id,
            )
            if membership is None:
                db.add(
                    TenantMembership(
                        tenant_id=tenant.id,
                        user_id=owner.id,
                        status=MembershipStatus.ACTIVE.value,
                        is_billing_contact=True,
                        created_at=tenant.created_at,
                    )
                )
            await assign(
                db,
                user=owner,
                role_key=owner_spec[3],
                scope_type=ScopeType.TENANT,
                scope_id=tenant.id,
                granted_by=admin.id,
            )

        config = await one(
            db,
            TenantConfiguration,
            TenantConfiguration.tenant_id == tenant.id,
            TenantConfiguration.version == 1,
        )
        if config is None:
            db.add(
                TenantConfiguration(
                    tenant_id=tenant.id,
                    version=1,
                    config={"locale": "en-US", "support_tier": plan_key, "onboarding_complete": True},
                    note="Initial configuration",
                    created_by=admin.id,
                    created_at=tenant.created_at,
                )
            )

        subscription = await one(db, Subscription, Subscription.tenant_id == tenant.id)
        if subscription is None:
            period_end = utcnow() + timedelta(days=365 if interval == BillingInterval.YEARLY else 30)
            subscription = Subscription(
                tenant_id=tenant.id,
                plan_id=plan.id,
                status=(
                    SubscriptionStatus.TRIALING.value
                    if status == TenantStatus.TRIAL
                    else SubscriptionStatus.ACTIVE.value
                    if status in (TenantStatus.ACTIVE, TenantStatus.PROVISIONING)
                    else SubscriptionStatus.PAST_DUE.value
                ),
                billing_interval=interval.value,
                current_period_start=ago(30),
                current_period_end=period_end,
                trial_ends_at=tenant.trial_ends_at,
                created_at=tenant.created_at,
            )
            db.add(subscription)
            await db.flush()

        if status in (TenantStatus.ACTIVE, TenantStatus.TRIAL):
            period = period_start()
            for metric, scale in (("api_calls", 10_000), ("storage_gb", 4), ("seats", 3)):
                limit = plan.limits.get(metric, 10_000)
                used = Decimal(rng.randint(scale, max(scale + 1, int(limit * 0.72))))
                key = f"seed:{tenant.id}:{metric}:{period.isoformat()}"
                if not await one(db, UsageRecord, UsageRecord.idempotency_key == key):
                    db.add(
                        UsageRecord(
                            tenant_id=tenant.id,
                            application_id=tenant.application_id,
                            metric=metric,
                            quantity=used,
                            period_start=period,
                            recorded_at=ago(rng.randint(0, 10)),
                            source="seed",
                            idempotency_key=key,
                            metadata_json={"seeded": True},
                        )
                    )

        number = f"INV-{tenant.created_at.year}-{tenant.id.hex[:6].upper()}"
        if not await one(db, Invoice, Invoice.number == number):
            amount = (
                plan.price_yearly_cents if interval == BillingInterval.YEARLY else plan.price_monthly_cents
            )
            issued = ago(rng.randint(3, 40))
            paid = rng.random() > 0.35
            invoice = Invoice(
                subscription_id=subscription.id,
                number=number,
                amount_cents=amount,
                currency="USD",
                status=InvoiceStatus.PAID.value if paid else InvoiceStatus.OPEN.value,
                period_start=ago(30),
                period_end=utcnow() + timedelta(days=30),
                issued_at=issued,
                due_at=issued + timedelta(days=14),
                paid_at=issued + timedelta(days=5) if paid else None,
                lines=[{"description": f"{plan.name} plan", "amount_cents": amount}],
                created_at=issued,
            )
            db.add(invoice)
            await db.flush()
            if paid:
                db.add(
                    Payment(
                        invoice_id=invoice.id,
                        provider="stripe",
                        provider_payment_id=f"seed_pi_{invoice.id.hex[:18]}",
                        amount_cents=amount,
                        currency="USD",
                        status="succeeded",
                        processed_at=invoice.paid_at,
                        created_at=invoice.paid_at or issued,
                    )
                )

    domain_specs = [
        ("northwind-sales", "sales.northwind.test", True, DomainStatus.VERIFIED),
        ("acme-cloud", "billing.acme.test", True, DomainStatus.VERIFIED),
        ("lumen-clinics", "care.lumen.test", True, DomainStatus.PENDING),
        ("orbital-storefront", "shop.orbital.test", True, DomainStatus.PENDING),
    ]
    for tenant_slug, domain_name, primary, domain_status in domain_specs:
        tenant = tenants[tenant_slug]
        if not await one(db, TenantDomain, TenantDomain.domain == domain_name):
            db.add(
                TenantDomain(
                    tenant_id=tenant.id,
                    domain=domain_name,
                    is_primary=primary,
                    status=domain_status.value,
                    verification_token=f"basa-verify={hashlib.sha256(domain_name.encode()).hexdigest()[:24]}",
                    ssl_status="issued" if domain_status == DomainStatus.VERIFIED else "pending",
                    ssl_expires_at=utcnow() + timedelta(days=120)
                    if domain_status == DomainStatus.VERIFIED
                    else None,
                    verified_at=tenant.created_at if domain_status == DomainStatus.VERIFIED else None,
                    created_at=tenant.created_at,
                )
            )

    flag_specs = [
        ("atlas-crm", "ai_lead_scoring", "AI lead scoring", True, 100),
        ("atlas-crm", "new_pipeline_board", "New pipeline board", True, 35),
        ("nova-billing", "usage_alerts", "Real-time usage alerts", True, 100),
        ("nova-billing", "invoice_pdf_v2", "Invoice PDF v2", False, 0),
        ("helix-identity", "passkeys", "Passkey sign-in", True, 60),
        ("helix-identity", "scim_provisioning", "SCIM provisioning", True, 100),
        ("lumen-care", "video_visits", "Video visits", True, 25),
        ("vertex-analytics", "semantic_layer", "Semantic layer", True, 80),
    ]
    flag_objects: dict[tuple[str, str], FeatureFlag] = {}
    for app_slug, key, name, enabled, rollout in flag_specs:
        flag = await one(
            db, FeatureFlag, FeatureFlag.application_id == apps[app_slug].id, FeatureFlag.key == key
        )
        if flag is None:
            flag = FeatureFlag(
                application_id=apps[app_slug].id,
                key=key,
                name=name,
                description=f"Controls {name.lower()} for this application.",
                enabled=enabled,
                rollout_percentage=rollout,
                created_by=admin.id,
            )
            db.add(flag)
            await db.flush()
        flag_objects[(app_slug, key)] = flag

    override_specs = [
        ("atlas-crm", "new_pipeline_board", "brightline-media", True),
        ("helix-identity", "passkeys", "lumen-health", False),
        ("vertex-analytics", "semantic_layer", "vertex-insights", True),
    ]
    for app_slug, flag_key, tenant_slug, enabled in override_specs:
        flag = flag_objects[(app_slug, flag_key)]
        tenant = tenants[tenant_slug]
        if not await one(
            db,
            FeatureFlagOverride,
            FeatureFlagOverride.flag_id == flag.id,
            FeatureFlagOverride.tenant_id == tenant.id,
        ):
            db.add(
                FeatureFlagOverride(
                    flag_id=flag.id,
                    tenant_id=tenant.id,
                    enabled=enabled,
                    created_at=ago(12),
                )
            )

    for app_slug in ("atlas-crm", "nova-billing", "helix-identity"):
        app = apps[app_slug]
        if not await one(
            db,
            ApplicationConfiguration,
            ApplicationConfiguration.application_id == app.id,
            ApplicationConfiguration.environment_id.is_(None),
            ApplicationConfiguration.version == 1,
        ):
            db.add(
                ApplicationConfiguration(
                    application_id=app.id,
                    environment_id=None,
                    version=1,
                    config={"support_email": "support@basa.dev", "audit_retention_days": 365},
                    note="Baseline application configuration",
                    created_by=admin.id,
                    created_at=ago(60),
                )
            )
        if not await one(
            db,
            Webhook,
            Webhook.application_id == app.id,
            Webhook.url == f"https://hooks.{app_slug}.test/basa",
        ):
            db.add(
                Webhook(
                    application_id=app.id,
                    url=f"https://hooks.{app_slug}.test/basa",
                    description="Platform event relay",
                    secret_encrypted=secret_box().encrypt(f"whsec_{uuid.uuid4().hex}"),
                    events=["tenant.created", "subscription.updated", "user.invited"],
                    active=True,
                )
            )
        if not await one(
            db, Integration, Integration.application_id == app.id, Integration.provider == "slack"
        ):
            db.add(
                Integration(
                    application_id=app.id,
                    provider="slack",
                    name="Slack notifications",
                    status="connected",
                    config={"channel": "#platform-alerts"},
                    created_at=ago(45),
                )
            )

    # Demo service credential (development only). The plaintext is intentionally
    # predictable so the local SDK demo works; rotate/remove before any shared use.
    demo_app = apps["atlas-crm"]
    demo_secret = "bsk_demo_atlas_sdk_secret_2026"
    if not await one(db, ApiCredential, ApiCredential.secret_hash == hash_token(demo_secret)):
        db.add(
            ApiCredential(
                application_id=demo_app.id,
                name="Local SDK demo",
                key_prefix=demo_secret[:18],
                secret_hash=hash_token(demo_secret),
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
                created_at=ago(20),
            )
        )

    # Demo OIDC login client (development only). The external demo app on
    # localhost:3003 uses these values to prove the hosted authorization-code flow.
    demo_client_id = "basa_demo_atlas_web"
    demo_client_secret = "bsc_demo_atlas_web_secret_2026"
    if not await one(db, ApplicationClient, ApplicationClient.client_id == demo_client_id):
        db.add(
            ApplicationClient(
                application_id=demo_app.id,
                name="Atlas CRM web (local demo)",
                client_id=demo_client_id,
                client_secret_hash=hash_token(demo_client_secret),
                client_type="confidential",
                redirect_uris=["http://localhost:3003/auth/callback"],
                post_logout_redirect_uris=["http://localhost:3003/"],
                allowed_origins=["http://localhost:3003"],
                scopes=["openid", "profile", "email", "offline_access"],
                pkce_required=True,
                status="active",
                created_by=admin.id,
            )
        )

    incidents_specs = [
        (
            "Elevated API latency",
            "p95 latency crossed 1.2s in production.",
            IncidentSeverity.SEV2,
            IncidentStatus.INVESTIGATING,
            "atlas-crm",
            "northwind-sales",
            0,
        ),
        (
            "Invoice generation backlog",
            "Queued invoice jobs are delayed.",
            IncidentSeverity.SEV3,
            IncidentStatus.MONITORING,
            "nova-billing",
            "acme-cloud",
            1,
        ),
        (
            "SSO callback failures",
            "A small subset of SSO callbacks returned 500.",
            IncidentSeverity.SEV1,
            IncidentStatus.RESOLVED,
            "helix-identity",
            "acme-workforce",
            8,
        ),
        (
            "Video visit capacity",
            "Media nodes approaching regional capacity.",
            IncidentSeverity.SEV2,
            IncidentStatus.OPEN,
            "lumen-care",
            "lumen-clinics",
            0,
        ),
        (
            "Warehouse query timeout",
            "Large semantic queries exceeded the 30s limit.",
            IncidentSeverity.SEV3,
            IncidentStatus.RESOLVED,
            "vertex-analytics",
            "vertex-insights",
            15,
        ),
    ]
    for title, description, severity, status, app_slug, tenant_slug, days in incidents_specs:
        if not await one(db, Incident, Incident.title == title):
            db.add(
                Incident(
                    application_id=apps[app_slug].id,
                    tenant_id=tenants[tenant_slug].id,
                    title=title,
                    description=description,
                    severity=severity.value,
                    status=status.value,
                    owner_id=internal["priya.nair@basa.dev"].id,
                    started_at=ago(days, 2),
                    resolved_at=ago(days, 1) if status == IncidentStatus.RESOLVED else None,
                    impact="Some customers experienced degraded performance.",
                    created_at=ago(days, 2),
                )
            )

    infra_specs = [
        (
            "atlas-api-prod-1",
            ResourceType.CONTAINER,
            "aws",
            "eu-west-1",
            ResourceStatus.HEALTHY,
            42_000,
            "atlas-crm",
        ),
        (
            "atlas-postgres-prod",
            ResourceType.DATABASE,
            "aws",
            "eu-west-1",
            ResourceStatus.HEALTHY,
            180_000,
            "atlas-crm",
        ),
        (
            "nova-worker-prod-1",
            ResourceType.CONTAINER,
            "gcp",
            "europe-west1",
            ResourceStatus.DEGRADED,
            36_000,
            "nova-billing",
        ),
        (
            "helix-api-prod-1",
            ResourceType.CONTAINER,
            "aws",
            "us-east-1",
            ResourceStatus.HEALTHY,
            51_000,
            "helix-identity",
        ),
        (
            "helix-redis-prod",
            ResourceType.MANAGED_SERVICE,
            "aws",
            "us-east-1",
            ResourceStatus.HEALTHY,
            22_000,
            "helix-identity",
        ),
        (
            "lumen-media-prod",
            ResourceType.SERVER,
            "azure",
            "westeurope",
            ResourceStatus.DEGRADED,
            96_000,
            "lumen-care",
        ),
        (
            "vertex-warehouse",
            ResourceType.DATABASE,
            "gcp",
            "europe-west1",
            ResourceStatus.HEALTHY,
            240_000,
            "vertex-analytics",
        ),
        (
            "orbital-cdn",
            ResourceType.NETWORK,
            "cloudflare",
            "global",
            ResourceStatus.HEALTHY,
            18_000,
            "orbital-commerce",
        ),
    ]
    for name, rtype, provider, region, status, cost, app_slug in infra_specs:
        if not await one(db, InfrastructureResource, InfrastructureResource.name == name):
            db.add(
                InfrastructureResource(
                    application_id=apps[app_slug].id,
                    name=name,
                    resource_type=rtype.value,
                    provider=provider,
                    region=region,
                    status=status.value,
                    monthly_cost_cents=cost,
                    metadata_json={"managed_by": "terraform"},
                )
            )

    for app_slug, env_slug, version, days, status in (
        ("atlas-crm", "production", "v2.18.4", 0, "succeeded"),
        ("atlas-crm", "staging", "v2.19.0-rc.2", 1, "succeeded"),
        ("nova-billing", "production", "v1.42.1", 2, "succeeded"),
        ("helix-identity", "production", "v4.6.0", 3, "succeeded"),
        ("helix-identity", "staging", "v4.7.0-rc.1", 0, "in_progress"),
        ("lumen-care", "production", "v3.11.2", 5, "succeeded"),
        ("vertex-analytics", "production", "v0.9.7", 7, "failed"),
        ("orbital-commerce", "staging", "v1.0.0-beta.3", 1, "rolled_back"),
    ):
        app = apps[app_slug]
        if not await one(db, Deployment, Deployment.application_id == app.id, Deployment.version == version):
            db.add(
                Deployment(
                    application_id=app.id,
                    environment_id=envs[(app_slug, env_slug)].id,
                    version=version,
                    commit_sha=hashlib.sha1(f"{app_slug}{version}".encode()).hexdigest(),
                    status=status,
                    deployed_by=internal["maya.chen@basa.dev"].id,
                    deployed_at=ago(days, rng.randint(1, 10)),
                    duration_seconds=rng.randint(90, 600),
                    metadata_json={"strategy": "rolling"},
                )
            )

    for name, severity, app_slug, status in (
        ("API error rate above 2%", "critical", "atlas-crm", "firing"),
        ("Worker queue depth growing", "warning", "nova-billing", "firing"),
        ("Media node memory pressure", "warning", "lumen-care", "firing"),
        ("Warehouse connection saturation", "info", "vertex-analytics", "resolved"),
    ):
        if not await one(db, MonitoringAlert, MonitoringAlert.name == name):
            db.add(
                MonitoringAlert(
                    application_id=apps[app_slug].id,
                    name=name,
                    severity=severity,
                    condition={"threshold": 2, "window": "5m"},
                    status=status,
                    last_triggered_at=ago(0, rng.randint(1, 12)),
                    resolved_at=ago(0, 1) if status == "resolved" else None,
                )
            )

    project_specs = [
        (
            "PLAT",
            "Platform reliability",
            "Reduce incident load and improve tenant isolation guarantees.",
            ProjectStatus.ACTIVE,
            "northwind-labs",
        ),
        (
            "SEC",
            "SOC 2 readiness",
            "Close control gaps ahead of the Type II observation window.",
            ProjectStatus.ACTIVE,
            "acme-cloud",
        ),
        (
            "ONB",
            "Tenant onboarding v2",
            "Self-service onboarding with domain verification and guided setup.",
            ProjectStatus.PLANNED,
            "lumen-health",
        ),
    ]
    projects: dict[str, Project] = {}
    for key, name, description, status, org_slug in project_specs:
        project = await one(db, Project, Project.organization_id == orgs[org_slug].id, Project.key == key)
        if project is None:
            project = Project(
                organization_id=orgs[org_slug].id,
                key=key,
                name=name,
                description=description,
                status=status.value,
                lead_id=internal["maya.chen@basa.dev"].id,
                target_date=(utcnow() + timedelta(days=rng.randint(20, 120))).date(),
                created_at=ago(rng.randint(20, 90)),
            )
            db.add(project)
            await db.flush()
        projects[key] = project

    task_specs = [
        ("PLAT", "Add tenant isolation regression suite", TaskStatus.IN_PROGRESS, TaskPriority.URGENT, 0),
        ("PLAT", "Automate refresh-token reuse alerts", TaskStatus.DONE, TaskPriority.HIGH, 4),
        ("PLAT", "Document disaster recovery drill", TaskStatus.TODO, TaskPriority.MEDIUM, 12),
        ("SEC", "Collect evidence for access reviews", TaskStatus.IN_REVIEW, TaskPriority.HIGH, 2),
        ("SEC", "Enable MFA enforcement for admins", TaskStatus.TODO, TaskPriority.URGENT, 6),
        ("SEC", "Rotate demo API credentials", TaskStatus.BACKLOG, TaskPriority.MEDIUM, 30),
        ("ONB", "Design guided domain verification", TaskStatus.IN_PROGRESS, TaskPriority.HIGH, 1),
        ("ONB", "Add invite acceptance email", TaskStatus.DONE, TaskPriority.HIGH, 5),
        ("ONB", "Pilot onboarding with Lumen", TaskStatus.TODO, TaskPriority.MEDIUM, 15),
        ("PLAT", "Backup restore drill in staging", TaskStatus.TODO, TaskPriority.HIGH, 9),
    ]
    for project_key, title, status, priority, due_days in task_specs:
        project = projects[project_key]
        if not await one(db, Task, Task.project_id == project.id, Task.title == title):
            task = Task(
                project_id=project.id,
                title=title,
                description=f"Tracked as part of {project.name}.",
                status=status.value,
                priority=priority.value,
                assignee_id=internal["maya.chen@basa.dev"].id
                if project_key == "PLAT"
                else internal["omar.haddad@basa.dev"].id,
                reporter_id=admin.id,
                due_date=(utcnow() + timedelta(days=due_days)).date(),
                labels=["platform", project_key.lower()],
                application_id=apps["atlas-crm"].id if project_key == "PLAT" else None,
                completed_at=utcnow() - timedelta(days=rng.randint(0, 5))
                if status == TaskStatus.DONE
                else None,
                created_at=ago(rng.randint(2, 40)),
            )
            db.add(task)
            await db.flush()
            if status == TaskStatus.IN_PROGRESS:
                db.add(
                    TaskComment(
                        task_id=task.id,
                        author_id=internal["priya.nair@basa.dev"].id,
                        body="Regression suite is running against the RLS test role now.",
                        created_at=ago(1),
                    )
                )

    for title, body, category, read, user in (
        (
            "Production deployment succeeded",
            "Atlas CRM v2.18.4 is live in production.",
            "deployment",
            False,
            admin,
        ),
        (
            "New tenant awaiting verification",
            "Orbital Retail was provisioned and needs domain verification.",
            "tenant",
            False,
            internal["maya.chen@basa.dev"],
        ),
        (
            "Invoice overdue",
            "Two invoices are past due and need follow-up.",
            "billing",
            True,
            internal["lena.ortiz@basa.dev"],
        ),
        (
            "Incident resolved",
            "SSO callback failures have been resolved.",
            "incident",
            True,
            internal["priya.nair@basa.dev"],
        ),
    ):
        if not await one(db, Notification, Notification.user_id == user.id, Notification.title == title):
            db.add(
                Notification(
                    user_id=user.id,
                    title=title,
                    body=body,
                    category=category,
                    read_at=ago(1) if read else None,
                    created_at=ago(rng.randint(0, 6)),
                )
            )

    audit_specs = [
        ("tenant.created", "tenant", "northwind-sales", "tenant", 2),
        ("application.updated", "application", "atlas-crm", "application", 3),
        ("plan.updated", "plan", "growth", "platform", 5),
        ("user.role_assigned", "user", "emma.wilson@northwind.dev", "platform", 6),
        ("tenant.domain_verification", "tenant_domain", "sales.northwind.test", "tenant", 8),
        ("subscription.updated", "subscription", "acme-cloud", "tenant", 10),
        ("credential.revoked", "api_credential", "legacy-atlas", "application", 12),
        ("security.policy_updated", "security_policy", "Platform default", "platform", 15),
        ("tenant.suspended", "tenant", "kestrel-logistics", "tenant", 20),
        ("invoice.paid", "invoice", "INV-2026-8A31C2", "tenant", 24),
        ("application.activated", "application", "helix-identity", "application", 30),
        ("tenant.ownership_transferred", "tenant", "lumen-clinics", "tenant", 35),
    ]
    for action, resource_type, resource_id, scope, days in audit_specs:
        if not await one(db, AuditLog, AuditLog.action == action, AuditLog.resource_id == resource_id):
            tenant_scope_id = None
            if scope == "tenant":
                tenant_scope_id = next((t.id for t in tenants.values() if t.slug == resource_id), None)
                if tenant_scope_id is None:
                    tenant_scope_id = tenants["northwind-sales"].id
            db.add(
                AuditLog(
                    actor_id=admin.id if days % 2 == 0 else internal["maya.chen@basa.dev"].id,
                    actor_email=admin.email if days % 2 == 0 else "maya.chen@basa.dev",
                    action=action,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    scope_type=scope,
                    scope_id=tenant_scope_id,
                    outcome="success",
                    ip_address="10.0.0.24",
                    user_agent="Basacore Console",
                    request_id=f"seed_{uuid.uuid4().hex[:10]}",
                    metadata_json={"seeded": True},
                    created_at=ago(days, rng.randint(0, 12)),
                )
            )

    await db.commit()
    print("Seed complete.")
    print(f"  Admin: {settings.seed_admin_email} / {settings.seed_admin_password}")
    print("  Demo tenant users use the same password (development only).")
    print(f"  SDK client_id: {demo_secret[:0] or ''}", end="")
    credential = await one(db, ApiCredential, ApiCredential.secret_hash == hash_token(demo_secret))
    if credential:
        print(credential.id)
        print(f"  SDK client_secret: {demo_secret}")
    print(f"  OIDC client_id: {demo_client_id}")
    print(f"  OIDC client_secret: {demo_client_secret}")


async def main(reference_only: bool) -> None:
    async with SessionLocal() as db:
        await sync_reference(db)
        if reference_only:
            print("Reference data synced.")
            return
        async with platform_scope(db):
            await seed_demo(db)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed Basacore data")
    parser.add_argument(
        "--reference",
        action="store_true",
        help="Only sync permissions, system roles and the default policy",
    )
    args = parser.parse_args()
    asyncio.run(main(args.reference))
