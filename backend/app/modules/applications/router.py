from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.db import get_session, slugify
from app.core.deps import Principal, require_platform
from app.core.enums import ApplicationStatus
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.pagination import PageParams, page_params, paginate
from app.core.security import generate_token, hash_token
from app.modules.applications import service
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
from app.modules.applications.schemas import (
    ApplicationClientCreate,
    ApplicationClientUpdate,
    ApplicationCreate,
    ApplicationUpdate,
    ConfigPublish,
    CredentialCreate,
    EnvironmentCreate,
    EnvironmentOut,
    EnvironmentUpdate,
    FeatureFlagCreate,
    FeatureFlagOverrideRequest,
    FeatureFlagUpdate,
    IntegrationCreate,
    StatusChange,
    WebhookCreate,
)
from app.modules.identity.models import User
from app.modules.organizations.models import Organization

router = APIRouter(prefix="/applications", tags=["Applications"])


async def _app_or_404(db: AsyncSession, application_id: uuid.UUID) -> Application:
    app = await db.get(Application, application_id)
    if app is None:
        raise NotFound("Application", str(application_id))
    return app


async def _slug(db: AsyncSession, base: str) -> str:
    candidate, n = slugify(base), 1
    while (
        await db.execute(select(Application.id).where(Application.slug == candidate))
    ).scalar_one_or_none():
        n += 1
        candidate = f"{slugify(base)}-{n}"
    return candidate


def _client_public(client: ApplicationClient) -> dict[str, Any]:
    return {
        "id": client.id,
        "application_id": client.application_id,
        "name": client.name,
        "client_id": client.client_id,
        "client_type": client.client_type,
        "redirect_uris": client.redirect_uris,
        "post_logout_redirect_uris": client.post_logout_redirect_uris,
        "allowed_origins": client.allowed_origins,
        "scopes": client.scopes,
        "pkce_required": client.pkce_required,
        "status": client.status,
        "last_used_at": client.last_used_at,
        "created_at": client.created_at,
        "updated_at": client.updated_at,
    }


async def _client_or_404(db: AsyncSession, application_id: uuid.UUID, client_id: str) -> ApplicationClient:
    client = (
        await db.execute(
            select(ApplicationClient).where(
                ApplicationClient.application_id == application_id,
                ApplicationClient.client_id == client_id,
            )
        )
    ).scalar_one_or_none()
    if client is None:
        raise NotFound("Application client", client_id)
    return client


async def _revoke_client_sessions(db: AsyncSession, client: ApplicationClient, reason: str) -> None:
    from app.core.db import utcnow
    from app.modules.applications.models import ApplicationSession

    await db.execute(
        ApplicationSession.__table__.update()
        .where(
            ApplicationSession.application_client_id == client.id,
            ApplicationSession.revoked_at.is_(None),
        )
        .values(revoked_at=utcnow(), revoked_reason=reason)
    )


@router.get("", response_model=dict, summary="List applications")
async def list_applications(
    params: PageParams = Depends(page_params),
    organization_id: uuid.UUID | None = None,
    app_status: ApplicationStatus | None = None,
    _: Principal = Depends(require_platform("applications.read")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    stmt = select(Application).order_by(Application.name)
    if params.search:
        term = f"%{params.search.strip()}%"
        stmt = stmt.where(or_(Application.name.ilike(term), Application.slug.ilike(term)))
    if organization_id:
        stmt = stmt.where(Application.organization_id == organization_id)
    if app_status:
        stmt = stmt.where(Application.status == app_status.value)
    apps, total = await paginate(db, stmt, params)
    stats = await service.application_stats(db, [a.id for a in apps])
    org_ids = {a.organization_id for a in apps}
    owners = {a.owner_id for a in apps if a.owner_id}
    org_names = dict(
        (
            await db.execute(
                select(Organization.id, Organization.name).where(
                    Organization.id.in_(org_ids or [uuid.uuid4()])
                )
            )
        ).all()
    )
    owner_names = dict(
        (await db.execute(select(User.id, User.full_name).where(User.id.in_(owners or [uuid.uuid4()])))).all()
    )
    items = []
    for app in apps:
        item = service.application_public(app)
        item.update(
            {
                "organization_name": org_names.get(app.organization_id),
                "owner_name": owner_names.get(app.owner_id),
                "counts": stats.get(app.id, {}),
            }
        )
        items.append(item)
    return {"items": items, "total": total, "limit": params.limit, "offset": params.offset}


@router.post("", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_application(
    payload: ApplicationCreate,
    request: Request,
    principal: Principal = Depends(require_platform("applications.create")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    org = await db.get(Organization, payload.organization_id)
    if org is None:
        raise NotFound("Organization", str(payload.organization_id))
    if payload.owner_id:
        owner = await db.get(User, payload.owner_id)
        if owner is None or owner.user_type != "internal":
            raise ValidationFailed("Application owners must be internal users.")

    slug = payload.slug or await _slug(db, payload.name)
    if (await db.execute(select(Application.id).where(Application.slug == slug))).scalar_one_or_none():
        raise Conflict("An application with that slug already exists.")

    app = Application(
        organization_id=payload.organization_id,
        name=payload.name.strip(),
        slug=slug,
        description=payload.description,
        owner_id=payload.owner_id,
        base_url=payload.base_url,
        status=ApplicationStatus.DRAFT.value,
        isolation_strategy=payload.isolation_strategy.value,
        auth_config=payload.auth_config,
        integration_config=payload.integration_config,
        resource_allocation=payload.resource_allocation,
    )
    db.add(app)
    await db.flush()
    env = service.default_environment(app.id)
    env.status = "pending"
    db.add(env)
    await record_audit(
        db,
        action="application.created",
        resource_type="application",
        resource_id=app.id,
        actor=principal,
        request=request,
        scope_type="application",
        scope_id=app.id,
        metadata={"name": app.name, "slug": app.slug, "isolation": app.isolation_strategy},
    )
    await db.commit()
    return {**service.application_public(app), "environment": EnvironmentOut.model_validate(env).model_dump()}


@router.get("/{application_id}", response_model=dict)
async def get_application(
    application_id: uuid.UUID,
    _: Principal = Depends(require_platform("applications.read")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    app = await _app_or_404(db, application_id)
    environments = (
        (
            await db.execute(
                select(ApplicationEnvironment)
                .where(ApplicationEnvironment.application_id == app.id)
                .order_by(ApplicationEnvironment.created_at)
            )
        )
        .scalars()
        .all()
    )
    flags = (
        (
            await db.execute(
                select(FeatureFlag).where(FeatureFlag.application_id == app.id).order_by(FeatureFlag.key)
            )
        )
        .scalars()
        .all()
    )
    credentials = (
        (
            await db.execute(
                select(ApiCredential)
                .where(ApiCredential.application_id == app.id)
                .order_by(ApiCredential.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    configs = (
        (
            await db.execute(
                select(ApplicationConfiguration)
                .where(ApplicationConfiguration.application_id == app.id)
                .order_by(ApplicationConfiguration.version.desc())
                .limit(20)
            )
        )
        .scalars()
        .all()
    )
    clients = (
        (
            await db.execute(
                select(ApplicationClient)
                .where(ApplicationClient.application_id == app.id)
                .order_by(ApplicationClient.created_at)
            )
        )
        .scalars()
        .all()
    )
    stats = (await service.application_stats(db, [app.id])).get(app.id, {})
    owner = await db.get(User, app.owner_id) if app.owner_id else None
    org = await db.get(Organization, app.organization_id)

    return {
        **service.application_public(app),
        "organization_name": org.name if org else None,
        "owner": ({"id": owner.id, "name": owner.full_name, "email": owner.email} if owner else None),
        "counts": stats,
        "environments": [EnvironmentOut.model_validate(e).model_dump() for e in environments],
        "flags": [
            {
                "id": f.id,
                "key": f.key,
                "name": f.name,
                "description": f.description,
                "enabled": f.enabled,
                "rollout_percentage": f.rollout_percentage,
                "updated_at": f.updated_at,
            }
            for f in flags
        ],
        "credentials": [
            {
                "id": c.id,
                "name": c.name,
                "key_prefix": c.key_prefix,
                "scopes": c.scopes,
                "status": c.status,
                "last_used_at": c.last_used_at,
                "expires_at": c.expires_at,
                "revoked_at": c.revoked_at,
                "created_at": c.created_at,
            }
            for c in credentials
        ],
        "config_history": [
            {
                "id": c.id,
                "version": c.version,
                "environment_id": c.environment_id,
                "note": c.note,
                "config": c.config,
                "created_at": c.created_at,
            }
            for c in configs
        ],
        "clients": [_client_public(c) for c in clients],
    }


@router.get("/{application_id}/clients", response_model=list[dict], summary="List login clients")
async def list_application_clients(
    application_id: uuid.UUID,
    _: Principal = Depends(require_platform("applications.read")),
    db: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    await _app_or_404(db, application_id)
    rows = (
        (
            await db.execute(
                select(ApplicationClient)
                .where(ApplicationClient.application_id == application_id)
                .order_by(ApplicationClient.created_at)
            )
        )
        .scalars()
        .all()
    )
    return [_client_public(client) for client in rows]


@router.post(
    "/{application_id}/clients",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
    summary="Create an OIDC login client",
)
async def create_application_client(
    application_id: uuid.UUID,
    payload: ApplicationClientCreate,
    request: Request,
    principal: Principal = Depends(require_platform("applications.update")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    app = await _app_or_404(db, application_id)
    client_id = f"basa_{generate_token(20)}"
    secret: str | None = None
    secret_hash: str | None = None
    if payload.client_type == "confidential":
        secret = f"bsc_{generate_token(32)}"
        secret_hash = hash_token(secret)

    client = ApplicationClient(
        application_id=app.id,
        name=payload.name.strip(),
        client_id=client_id,
        client_secret_hash=secret_hash,
        client_type=payload.client_type,
        redirect_uris=payload.redirect_uris,
        post_logout_redirect_uris=payload.post_logout_redirect_uris,
        allowed_origins=payload.allowed_origins,
        scopes=payload.scopes,
        pkce_required=True if payload.client_type == "public" else payload.pkce_required,
        status="active",
        created_by=principal.user.id,
    )
    db.add(client)
    await db.flush()
    await record_audit(
        db,
        action="application_client.created",
        resource_type="application_client",
        resource_id=client.id,
        actor=principal,
        request=request,
        scope_type="application",
        scope_id=app.id,
        metadata={
            "client_id": client.client_id,
            "client_type": client.client_type,
            "redirect_uris": client.redirect_uris,
            "scopes": client.scopes,
        },
    )
    await db.commit()
    return {**_client_public(client), "client_secret": secret}


@router.patch("/{application_id}/clients/{client_id}", response_model=dict, summary="Update a login client")
async def update_application_client(
    application_id: uuid.UUID,
    client_id: str,
    payload: ApplicationClientUpdate,
    request: Request,
    principal: Principal = Depends(require_platform("applications.update")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    client = await _client_or_404(db, application_id, client_id)
    changes = payload.model_dump(exclude_unset=True)
    if client.client_type == "public" and changes.get("pkce_required") is False:
        raise ValidationFailed("Public clients must use PKCE. It cannot be disabled.")
    if changes.get("status") == "disabled":
        await _revoke_client_sessions(db, client, "client_disabled")
    for field, value in changes.items():
        if value is not None:
            setattr(client, field, value)
    await record_audit(
        db,
        action="application_client.updated",
        resource_type="application_client",
        resource_id=client.id,
        actor=principal,
        request=request,
        scope_type="application",
        scope_id=application_id,
        metadata={"fields": sorted(changes)},
    )
    await db.commit()
    return _client_public(client)


@router.post(
    "/{application_id}/clients/{client_id}/rotate-secret",
    response_model=dict,
    summary="Rotate a confidential client secret",
)
async def rotate_application_client_secret(
    application_id: uuid.UUID,
    client_id: str,
    request: Request,
    principal: Principal = Depends(require_platform("applications.update")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    client = await _client_or_404(db, application_id, client_id)
    if client.client_type != "confidential":
        raise ValidationFailed("Public clients do not have a client secret. They use PKCE.")
    secret = f"bsc_{generate_token(32)}"
    client.client_secret_hash = hash_token(secret)
    await record_audit(
        db,
        action="application_client.secret_rotated",
        resource_type="application_client",
        resource_id=client.id,
        actor=principal,
        request=request,
        scope_type="application",
        scope_id=application_id,
        metadata={"client_id": client.client_id},
    )
    await db.commit()
    return {"client_id": client.client_id, "client_secret": secret}


@router.delete(
    "/{application_id}/clients/{client_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Disable a login client and revoke its sessions",
)
async def disable_application_client(
    application_id: uuid.UUID,
    client_id: str,
    request: Request,
    principal: Principal = Depends(require_platform("applications.update")),
    db: AsyncSession = Depends(get_session),
) -> Response:
    client = await _client_or_404(db, application_id, client_id)
    client.status = "disabled"
    await _revoke_client_sessions(db, client, "client_disabled")
    await record_audit(
        db,
        action="application_client.disabled",
        resource_type="application_client",
        resource_id=client.id,
        actor=principal,
        request=request,
        scope_type="application",
        scope_id=application_id,
        metadata={"client_id": client.client_id},
    )
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/{application_id}", response_model=dict)
async def update_application(
    application_id: uuid.UUID,
    payload: ApplicationUpdate,
    request: Request,
    principal: Principal = Depends(require_platform("applications.update")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    app = await _app_or_404(db, application_id)
    changes = payload.model_dump(exclude_unset=True)
    if "owner_id" in changes and changes["owner_id"]:
        owner = await db.get(User, changes["owner_id"])
        if owner is None or owner.user_type != "internal":
            raise ValidationFailed("Application owners must be internal users.")
    for field, value in changes.items():
        if value is not None:
            setattr(app, field, value.value if hasattr(value, "value") else value)
    await record_audit(
        db,
        action="application.updated",
        resource_type="application",
        resource_id=app.id,
        actor=principal,
        request=request,
        scope_type="application",
        scope_id=app.id,
        metadata={"fields": sorted(changes)},
    )
    await db.commit()
    return service.application_public(app)


@router.post("/{application_id}/status", response_model=dict)
async def change_application_status(
    application_id: uuid.UUID,
    payload: StatusChange,
    request: Request,
    principal: Principal = Depends(require_platform("applications.activate")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    app = await _app_or_404(db, application_id)
    if payload.status == ApplicationStatus.ACTIVE:
        env_count = (
            await db.execute(
                select(func.count())
                .select_from(ApplicationEnvironment)
                .where(ApplicationEnvironment.application_id == app.id)
            )
        ).scalar_one()
        if not env_count:
            raise ValidationFailed("Configure at least one environment before activating.")
        if not app.base_url:
            raise ValidationFailed("A base URL is required before activation.")
    app.status = payload.status.value
    if payload.status == ApplicationStatus.ARCHIVED:
        from app.core.db import utcnow

        app.archived_at = utcnow()
    await record_audit(
        db,
        action=f"application.{payload.status.value}",
        resource_type="application",
        resource_id=app.id,
        actor=principal,
        request=request,
        scope_type="application",
        scope_id=app.id,
        metadata={"reason": payload.reason},
    )
    await db.commit()
    return service.application_public(app)


@router.get("/{application_id}/environments", response_model=list[EnvironmentOut])
async def list_environments(
    application_id: uuid.UUID,
    _: Principal = Depends(require_platform("applications.read")),
    db: AsyncSession = Depends(get_session),
) -> list[ApplicationEnvironment]:
    await _app_or_404(db, application_id)
    rows = (
        (
            await db.execute(
                select(ApplicationEnvironment)
                .where(ApplicationEnvironment.application_id == application_id)
                .order_by(ApplicationEnvironment.created_at)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


@router.post("/{application_id}/environments", response_model=EnvironmentOut, status_code=201)
async def create_environment(
    application_id: uuid.UUID,
    payload: EnvironmentCreate,
    request: Request,
    principal: Principal = Depends(require_platform("environments.manage")),
    db: AsyncSession = Depends(get_session),
) -> ApplicationEnvironment:
    app = await _app_or_404(db, application_id)
    exists = (
        await db.execute(
            select(ApplicationEnvironment.id).where(
                ApplicationEnvironment.application_id == app.id,
                ApplicationEnvironment.slug == payload.slug,
            )
        )
    ).scalar_one_or_none()
    if exists:
        raise Conflict("This environment slug already exists for the application.")
    if payload.is_default:
        await db.execute(
            ApplicationEnvironment.__table__.update()
            .where(ApplicationEnvironment.application_id == app.id)
            .values(is_default=False)
        )
    env = ApplicationEnvironment(
        application_id=app.id,
        name=payload.name,
        slug=payload.slug,
        kind=payload.kind.value,
        base_url=payload.base_url,
        api_base_url=payload.api_base_url,
        status="pending",
        is_default=payload.is_default,
        config=payload.config,
    )
    db.add(env)
    await record_audit(
        db,
        action="environment.created",
        resource_type="application_environment",
        resource_id=env.id,
        actor=principal,
        request=request,
        scope_type="application",
        scope_id=app.id,
        metadata={"slug": env.slug, "kind": env.kind},
    )
    await db.commit()
    await db.refresh(env)
    return env


@router.patch("/{application_id}/environments/{environment_id}", response_model=EnvironmentOut)
async def update_environment(
    application_id: uuid.UUID,
    environment_id: uuid.UUID,
    payload: EnvironmentUpdate,
    request: Request,
    principal: Principal = Depends(require_platform("environments.manage")),
    db: AsyncSession = Depends(get_session),
) -> ApplicationEnvironment:
    env = await db.get(ApplicationEnvironment, environment_id)
    if env is None or env.application_id != application_id:
        raise NotFound("Environment", str(environment_id))
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("is_default"):
        await db.execute(
            ApplicationEnvironment.__table__.update()
            .where(ApplicationEnvironment.application_id == application_id)
            .values(is_default=False)
        )
    for field, value in changes.items():
        if value is not None:
            setattr(env, field, value)
    await record_audit(
        db,
        action="environment.updated",
        resource_type="application_environment",
        resource_id=env.id,
        actor=principal,
        request=request,
        scope_type="application",
        scope_id=application_id,
        metadata={"fields": sorted(changes)},
    )
    await db.commit()
    return env


@router.get("/{application_id}/credentials", response_model=list[dict])
async def list_credentials(
    application_id: uuid.UUID,
    _: Principal = Depends(require_platform("credentials.read")),
    db: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    await _app_or_404(db, application_id)
    rows = (
        (
            await db.execute(
                select(ApiCredential)
                .where(ApiCredential.application_id == application_id)
                .order_by(ApiCredential.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": c.id,
            "name": c.name,
            "key_prefix": c.key_prefix,
            "scopes": c.scopes,
            "status": c.status,
            "last_used_at": c.last_used_at,
            "expires_at": c.expires_at,
            "revoked_at": c.revoked_at,
            "created_at": c.created_at,
        }
        for c in rows
    ]


@router.post("/{application_id}/credentials", response_model=dict, status_code=201)
async def create_credential(
    application_id: uuid.UUID,
    payload: CredentialCreate,
    request: Request,
    principal: Principal = Depends(require_platform("credentials.manage")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    app = await _app_or_404(db, application_id)
    credential, plaintext = await service.issue_credential(
        db,
        application=app,
        name=payload.name,
        scopes=payload.scopes,
        created_by=principal.user.id,
        expires_in_days=payload.expires_in_days,
    )
    await record_audit(
        db,
        action="credential.created",
        resource_type="api_credential",
        resource_id=credential.id,
        actor=principal,
        request=request,
        scope_type="application",
        scope_id=app.id,
        metadata={"name": credential.name, "scopes": credential.scopes},
    )
    await db.commit()
    return {
        "id": credential.id,
        "name": credential.name,
        "key_prefix": credential.key_prefix,
        "scopes": credential.scopes,
        "client_id": str(credential.id),
        "client_secret": plaintext,
        "expires_at": credential.expires_at,
        "warning": "The client secret is shown once and cannot be retrieved again.",
    }


@router.delete("/{application_id}/credentials/{credential_id}", status_code=204)
async def revoke_credential(
    application_id: uuid.UUID,
    credential_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_platform("credentials.manage")),
    db: AsyncSession = Depends(get_session),
) -> None:
    credential = await db.get(ApiCredential, credential_id)
    if credential is None or credential.application_id != application_id:
        raise NotFound("Credential", str(credential_id))
    from app.core.db import utcnow

    credential.revoked_at = utcnow()
    credential.status = "revoked"
    await record_audit(
        db,
        action="credential.revoked",
        resource_type="api_credential",
        resource_id=credential.id,
        actor=principal,
        request=request,
        scope_type="application",
        scope_id=application_id,
    )
    await db.commit()


@router.get("/{application_id}/config", response_model=dict)
async def get_configuration(
    application_id: uuid.UUID,
    environment_id: uuid.UUID | None = None,
    _: Principal = Depends(require_platform("config.read")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    await _app_or_404(db, application_id)
    latest_app = await service.latest_config(db, application_id, None)
    latest_env = await service.latest_config(db, application_id, environment_id) if environment_id else None
    return {
        "application": (
            {"version": latest_app.version, "config": latest_app.config, "created_at": latest_app.created_at}
            if latest_app
            else None
        ),
        "environment": (
            {"version": latest_env.version, "config": latest_env.config, "created_at": latest_env.created_at}
            if latest_env
            else None
        ),
        "effective": {
            **(latest_app.config if latest_app else {}),
            **(latest_env.config if latest_env else {}),
        },
    }


@router.post("/{application_id}/config", response_model=dict, status_code=201)
async def publish_configuration(
    application_id: uuid.UUID,
    payload: ConfigPublish,
    request: Request,
    principal: Principal = Depends(require_platform("config.manage")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    app = await _app_or_404(db, application_id)
    if payload.environment_id:
        env = await db.get(ApplicationEnvironment, payload.environment_id)
        if env is None or env.application_id != app.id:
            raise NotFound("Environment", str(payload.environment_id))
    record = await service.publish_config(
        db,
        application_id=app.id,
        environment_id=payload.environment_id,
        config=payload.config,
        note=payload.note,
        created_by=principal.user.id,
    )
    await record_audit(
        db,
        action="config.published",
        resource_type="application_configuration",
        resource_id=record.id,
        actor=principal,
        request=request,
        scope_type="application",
        scope_id=app.id,
        metadata={
            "version": record.version,
            "environment_id": str(payload.environment_id) if payload.environment_id else None,
        },
    )
    await db.commit()
    return {"id": record.id, "version": record.version, "created_at": record.created_at}


@router.get("/{application_id}/flags", response_model=list[dict])
async def list_flags(
    application_id: uuid.UUID,
    _: Principal = Depends(require_platform("flags.read")),
    db: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    await _app_or_404(db, application_id)
    rows = (
        (
            await db.execute(
                select(FeatureFlag)
                .where(FeatureFlag.application_id == application_id)
                .order_by(FeatureFlag.key)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": f.id,
            "key": f.key,
            "name": f.name,
            "description": f.description,
            "enabled": f.enabled,
            "rollout_percentage": f.rollout_percentage,
            "updated_at": f.updated_at,
        }
        for f in rows
    ]


@router.post("/{application_id}/flags", response_model=dict, status_code=201)
async def create_flag(
    application_id: uuid.UUID,
    payload: FeatureFlagCreate,
    request: Request,
    principal: Principal = Depends(require_platform("flags.manage")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    app = await _app_or_404(db, application_id)
    exists = (
        await db.execute(
            select(FeatureFlag.id).where(
                FeatureFlag.application_id == application_id, FeatureFlag.key == payload.key
            )
        )
    ).scalar_one_or_none()
    if exists:
        raise Conflict("A flag with that key already exists.")
    flag = FeatureFlag(
        application_id=app.id,
        key=payload.key,
        name=payload.name,
        description=payload.description,
        enabled=payload.enabled,
        rollout_percentage=payload.rollout_percentage,
        created_by=principal.user.id,
    )
    db.add(flag)
    await record_audit(
        db,
        action="flag.created",
        resource_type="feature_flag",
        resource_id=flag.id,
        actor=principal,
        request=request,
        scope_type="application",
        scope_id=app.id,
        metadata={"key": flag.key},
    )
    await db.commit()
    return {"id": flag.id, "key": flag.key, "enabled": flag.enabled}


@router.patch("/{application_id}/flags/{flag_id}", response_model=dict)
async def update_flag(
    application_id: uuid.UUID,
    flag_id: uuid.UUID,
    payload: FeatureFlagUpdate,
    request: Request,
    principal: Principal = Depends(require_platform("flags.manage")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    flag = await db.get(FeatureFlag, flag_id)
    if flag is None or flag.application_id != application_id:
        raise NotFound("Feature flag", str(flag_id))
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        if value is not None:
            setattr(flag, field, value)
    await record_audit(
        db,
        action="flag.updated",
        resource_type="feature_flag",
        resource_id=flag.id,
        actor=principal,
        request=request,
        scope_type="application",
        scope_id=application_id,
        metadata={"changes": changes},
    )
    await db.commit()
    return {
        "id": flag.id,
        "key": flag.key,
        "enabled": flag.enabled,
        "rollout_percentage": flag.rollout_percentage,
    }


@router.put("/{application_id}/flags/{flag_id}/overrides/{tenant_id}", response_model=dict)
async def set_flag_override(
    application_id: uuid.UUID,
    flag_id: uuid.UUID,
    tenant_id: uuid.UUID,
    payload: FeatureFlagOverrideRequest,
    request: Request,
    principal: Principal = Depends(require_platform("flags.manage")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    from app.core.db import utcnow
    from app.modules.tenants.models import Tenant

    flag = await db.get(FeatureFlag, flag_id)
    if flag is None or flag.application_id != application_id:
        raise NotFound("Feature flag", str(flag_id))
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None or tenant.application_id != application_id:
        raise NotFound("Tenant", str(tenant_id))
    override = (
        await db.execute(
            select(FeatureFlagOverride).where(
                FeatureFlagOverride.flag_id == flag.id, FeatureFlagOverride.tenant_id == tenant.id
            )
        )
    ).scalar_one_or_none()
    if override is None:
        override = FeatureFlagOverride(
            flag_id=flag.id, tenant_id=tenant.id, enabled=payload.enabled, created_at=utcnow()
        )
        db.add(override)
    else:
        override.enabled = payload.enabled
    await record_audit(
        db,
        action="flag.override_set",
        resource_type="feature_flag",
        resource_id=flag.id,
        actor=principal,
        request=request,
        scope_type="tenant",
        scope_id=tenant.id,
        metadata={"tenant_id": str(tenant.id), "enabled": payload.enabled},
    )
    await db.commit()
    return {"flag_id": flag.id, "tenant_id": tenant.id, "enabled": payload.enabled}


@router.get("/{application_id}/integrations", response_model=list[dict])
async def list_integrations(
    application_id: uuid.UUID,
    _: Principal = Depends(require_platform("integrations.read")),
    db: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    await _app_or_404(db, application_id)
    rows = (
        (
            await db.execute(
                select(Integration)
                .where(Integration.application_id == application_id)
                .order_by(Integration.provider)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": i.id,
            "provider": i.provider,
            "name": i.name,
            "status": i.status,
            "config": i.config,
            "created_at": i.created_at,
        }
        for i in rows
    ]


@router.post("/{application_id}/integrations", response_model=dict, status_code=201)
async def create_integration(
    application_id: uuid.UUID,
    payload: IntegrationCreate,
    request: Request,
    principal: Principal = Depends(require_platform("integrations.manage")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    app = await _app_or_404(db, application_id)
    integration = Integration(
        application_id=app.id,
        provider=payload.provider,
        name=payload.name,
        status="connected",
        config=payload.config,
    )
    db.add(integration)
    await record_audit(
        db,
        action="integration.connected",
        resource_type="integration",
        resource_id=integration.id,
        actor=principal,
        request=request,
        scope_type="application",
        scope_id=app.id,
        metadata={"provider": payload.provider},
    )
    await db.commit()
    return {"id": integration.id, "provider": integration.provider, "status": integration.status}


@router.get("/{application_id}/webhooks", response_model=list[dict])
async def list_webhooks(
    application_id: uuid.UUID,
    _: Principal = Depends(require_platform("integrations.read")),
    db: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    await _app_or_404(db, application_id)
    rows = (
        (
            await db.execute(
                select(Webhook)
                .where(Webhook.application_id == application_id)
                .order_by(Webhook.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": w.id,
            "url": w.url,
            "description": w.description,
            "events": w.events,
            "active": w.active,
            "last_delivery_at": w.last_delivery_at,
            "failure_count": w.failure_count,
            "created_at": w.created_at,
        }
        for w in rows
    ]


@router.post("/{application_id}/webhooks", response_model=dict, status_code=201)
async def create_webhook(
    application_id: uuid.UUID,
    payload: WebhookCreate,
    request: Request,
    principal: Principal = Depends(require_platform("integrations.manage")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    app = await _app_or_404(db, application_id)
    secret = f"whsec_{service.generate_webhook_token()}"
    webhook = Webhook(
        application_id=app.id,
        url=payload.url,
        description=payload.description,
        secret_encrypted=service.encrypt_webhook_secret(secret),
        events=sorted(set(payload.events)),
        active=payload.active,
    )
    db.add(webhook)
    await record_audit(
        db,
        action="webhook.created",
        resource_type="webhook",
        resource_id=webhook.id,
        actor=principal,
        request=request,
        scope_type="application",
        scope_id=app.id,
        metadata={"url": payload.url, "events": payload.events},
    )
    await db.commit()
    return {
        "id": webhook.id,
        "url": webhook.url,
        "secret": secret,
        "warning": "The signing secret is shown once and cannot be retrieved again.",
    }
