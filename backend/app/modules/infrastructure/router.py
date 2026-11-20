from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.db import get_session, utcnow
from app.core.deps import Principal, require_platform
from app.core.enums import DeploymentStatus, ResourceStatus, ResourceType
from app.core.errors import NotFound, ValidationFailed
from app.core.pagination import PageParams, page_params, paginate
from app.modules.applications.models import Application, ApplicationEnvironment
from app.modules.infrastructure.models import (
    Deployment,
    Incident,
    InfrastructureResource,
    MonitoringAlert,
)

router = APIRouter(prefix="/infrastructure", tags=["Infrastructure"])
incidents_router = APIRouter(prefix="/incidents", tags=["Incidents"])
monitoring_router = APIRouter(prefix="/monitoring", tags=["Monitoring"])


class ResourceCreate(BaseModel):
    application_id: uuid.UUID | None = None
    environment_id: uuid.UUID | None = None
    name: str = Field(min_length=2, max_length=160)
    resource_type: ResourceType
    provider: str = Field(min_length=2, max_length=60)
    region: str | None = Field(default=None, max_length=60)
    monthly_cost_cents: int = Field(default=0, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DeploymentCreate(BaseModel):
    application_id: uuid.UUID
    environment_id: uuid.UUID
    version: str = Field(min_length=1, max_length=80)
    commit_sha: str | None = Field(default=None, max_length=64)
    status: DeploymentStatus = DeploymentStatus.SUCCEEDED
    duration_seconds: int | None = Field(default=None, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class IncidentCreate(BaseModel):
    title: str = Field(min_length=3, max_length=300)
    description: str | None = Field(default=None, max_length=4000)
    severity: str = Field(pattern=r"^(sev1|sev2|sev3|sev4)$")
    application_id: uuid.UUID | None = None
    tenant_id: uuid.UUID | None = None
    impact: str | None = Field(default=None, max_length=1000)


class IncidentUpdate(BaseModel):
    status: str | None = Field(default=None, pattern=r"^(open|investigating|monitoring|resolved)$")
    severity: str | None = Field(default=None, pattern=r"^(sev1|sev2|sev3|sev4)$")
    owner_id: uuid.UUID | None = None
    impact: str | None = Field(default=None, max_length=1000)
    postmortem: str | None = Field(default=None, max_length=8000)


@router.get("/resources", response_model=dict)
async def list_resources(
    params: PageParams = Depends(page_params),
    application_id: uuid.UUID | None = None,
    resource_type: ResourceType | None = None,
    resource_status: ResourceStatus | None = None,
    _: Principal = Depends(require_platform("infrastructure.read")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    stmt = select(InfrastructureResource).order_by(InfrastructureResource.name)
    if params.search:
        stmt = stmt.where(InfrastructureResource.name.ilike(f"%{params.search.strip()}%"))
    if application_id:
        stmt = stmt.where(InfrastructureResource.application_id == application_id)
    if resource_type:
        stmt = stmt.where(InfrastructureResource.resource_type == resource_type.value)
    if resource_status:
        stmt = stmt.where(InfrastructureResource.status == resource_status.value)
    rows, total = await paginate(db, stmt, params)
    return {
        "items": [
            {
                "id": r.id,
                "name": r.name,
                "resource_type": r.resource_type,
                "provider": r.provider,
                "region": r.region,
                "status": r.status,
                "application_id": r.application_id,
                "environment_id": r.environment_id,
                "monthly_cost_cents": r.monthly_cost_cents,
                "metadata": r.metadata_json,
                "created_at": r.created_at,
            }
            for r in rows
        ],
        "total": total,
        "limit": params.limit,
        "offset": params.offset,
    }


@router.get("/summary", response_model=dict)
async def infrastructure_summary(
    _: Principal = Depends(require_platform("infrastructure.read")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    by_type = (
        await db.execute(
            select(
                InfrastructureResource.resource_type,
                func.count(),
                func.coalesce(func.sum(InfrastructureResource.monthly_cost_cents), 0),
            ).group_by(InfrastructureResource.resource_type)
        )
    ).all()
    by_status = dict(
        (
            await db.execute(
                select(InfrastructureResource.status, func.count()).group_by(InfrastructureResource.status)
            )
        ).all()
    )
    monthly = (
        await db.execute(select(func.coalesce(func.sum(InfrastructureResource.monthly_cost_cents), 0)))
    ).scalar_one()
    return {
        "total_resources": sum(row[1] for row in by_type),
        "by_type": [{"type": row[0], "count": row[1], "monthly_cost_cents": int(row[2])} for row in by_type],
        "by_status": by_status,
        "monthly_cost_cents": int(monthly),
    }


@router.post("/resources", response_model=dict, status_code=201)
async def create_resource(
    payload: ResourceCreate,
    request: Request,
    principal: Principal = Depends(require_platform("infrastructure.manage")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    if payload.application_id and await db.get(Application, payload.application_id) is None:
        raise NotFound("Application", str(payload.application_id))
    resource = InfrastructureResource(
        application_id=payload.application_id,
        environment_id=payload.environment_id,
        name=payload.name,
        resource_type=payload.resource_type.value,
        provider=payload.provider,
        region=payload.region,
        status=ResourceStatus.HEALTHY.value,
        monthly_cost_cents=payload.monthly_cost_cents,
        metadata_json=payload.metadata,
    )
    db.add(resource)
    await record_audit(
        db,
        action="infrastructure.resource_created",
        resource_type="infrastructure_resource",
        resource_id=resource.id,
        actor=principal,
        request=request,
        scope_type="application" if payload.application_id else "platform",
        scope_id=payload.application_id,
        metadata={"name": resource.name, "type": resource.resource_type},
    )
    await db.commit()
    return {"id": resource.id}


@router.get("/deployments", response_model=dict)
async def list_deployments(
    params: PageParams = Depends(page_params),
    application_id: uuid.UUID | None = None,
    environment_id: uuid.UUID | None = None,
    deployment_status: DeploymentStatus | None = None,
    _: Principal = Depends(require_platform("deployments.read")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    stmt = (
        select(Deployment, Application.name, ApplicationEnvironment.name)
        .join(Application, Application.id == Deployment.application_id)
        .join(ApplicationEnvironment, ApplicationEnvironment.id == Deployment.environment_id)
        .order_by(Deployment.deployed_at.desc())
    )
    if application_id:
        stmt = stmt.where(Deployment.application_id == application_id)
    if environment_id:
        stmt = stmt.where(Deployment.environment_id == environment_id)
    if deployment_status:
        stmt = stmt.where(Deployment.status == deployment_status.value)
    total = int((await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one())
    rows = (await db.execute(stmt.limit(params.limit).offset(params.offset))).all()
    return {
        "items": [
            {
                "id": d.id,
                "application_id": d.application_id,
                "application_name": app_name,
                "environment_id": d.environment_id,
                "environment_name": env_name,
                "version": d.version,
                "commit_sha": d.commit_sha,
                "status": d.status,
                "deployed_by": d.deployed_by,
                "deployed_at": d.deployed_at,
                "duration_seconds": d.duration_seconds,
                "metadata": d.metadata_json,
            }
            for d, app_name, env_name in rows
        ],
        "total": total,
        "limit": params.limit,
        "offset": params.offset,
    }


@router.post("/deployments", response_model=dict, status_code=201)
async def record_deployment(
    payload: DeploymentCreate,
    request: Request,
    principal: Principal = Depends(require_platform("deployments.manage")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    env = await db.get(ApplicationEnvironment, payload.environment_id)
    if env is None or env.application_id != payload.application_id:
        raise ValidationFailed("The environment does not belong to the application.")
    deployment = Deployment(
        application_id=payload.application_id,
        environment_id=payload.environment_id,
        version=payload.version,
        commit_sha=payload.commit_sha,
        status=payload.status.value,
        deployed_by=principal.user.id,
        deployed_at=utcnow(),
        duration_seconds=payload.duration_seconds,
        metadata_json=payload.metadata,
    )
    db.add(deployment)
    if payload.status == DeploymentStatus.SUCCEEDED:
        env.status = "active"
    elif payload.status == DeploymentStatus.FAILED:
        env.status = "degraded"
    await record_audit(
        db,
        action="deployment.recorded",
        resource_type="deployment",
        resource_id=deployment.id,
        actor=principal,
        request=request,
        scope_type="application",
        scope_id=payload.application_id,
        metadata={"version": payload.version, "status": payload.status.value},
    )
    await db.commit()
    return {"id": deployment.id, "status": deployment.status}


@incidents_router.get("", response_model=dict)
async def list_incidents(
    params: PageParams = Depends(page_params),
    incident_status: str | None = None,
    severity: str | None = None,
    application_id: uuid.UUID | None = None,
    _: Principal = Depends(require_platform("incidents.read")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    stmt = (
        select(Incident, Application.name)
        .join(Application, Application.id == Incident.application_id, isouter=True)
        .order_by(Incident.started_at.desc())
    )
    if params.search:
        stmt = stmt.where(Incident.title.ilike(f"%{params.search.strip()}%"))
    if incident_status:
        stmt = stmt.where(Incident.status == incident_status)
    if severity:
        stmt = stmt.where(Incident.severity == severity)
    if application_id:
        stmt = stmt.where(Incident.application_id == application_id)
    total = int((await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one())
    rows = (await db.execute(stmt.limit(params.limit).offset(params.offset))).all()
    return {
        "items": [
            {
                "id": i.id,
                "title": i.title,
                "description": i.description,
                "severity": i.severity,
                "status": i.status,
                "application_id": i.application_id,
                "application_name": app_name,
                "tenant_id": i.tenant_id,
                "owner_id": i.owner_id,
                "impact": i.impact,
                "started_at": i.started_at,
                "resolved_at": i.resolved_at,
                "postmortem": i.postmortem,
            }
            for i, app_name in rows
        ],
        "total": total,
        "limit": params.limit,
        "offset": params.offset,
    }


@incidents_router.post("", response_model=dict, status_code=201)
async def create_incident(
    payload: IncidentCreate,
    request: Request,
    principal: Principal = Depends(require_platform("incidents.manage")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    incident = Incident(
        title=payload.title,
        description=payload.description,
        severity=payload.severity,
        status="open",
        application_id=payload.application_id,
        tenant_id=payload.tenant_id,
        impact=payload.impact,
        started_at=utcnow(),
    )
    db.add(incident)
    await record_audit(
        db,
        action="incident.opened",
        resource_type="incident",
        resource_id=incident.id,
        actor=principal,
        request=request,
        scope_type="application" if payload.application_id else "platform",
        scope_id=payload.application_id,
        metadata={"severity": incident.severity, "title": incident.title},
    )
    await db.commit()
    return {"id": incident.id, "status": incident.status}


@incidents_router.get("/{incident_id}", response_model=dict)
async def get_incident(
    incident_id: uuid.UUID,
    _: Principal = Depends(require_platform("incidents.read")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    incident = await db.get(Incident, incident_id)
    if incident is None:
        raise NotFound("Incident", str(incident_id))
    return {
        "id": incident.id,
        "title": incident.title,
        "description": incident.description,
        "severity": incident.severity,
        "status": incident.status,
        "application_id": incident.application_id,
        "tenant_id": incident.tenant_id,
        "owner_id": incident.owner_id,
        "impact": incident.impact,
        "started_at": incident.started_at,
        "resolved_at": incident.resolved_at,
        "postmortem": incident.postmortem,
    }


@incidents_router.patch("/{incident_id}", response_model=dict)
async def update_incident(
    incident_id: uuid.UUID,
    payload: IncidentUpdate,
    request: Request,
    principal: Principal = Depends(require_platform("incidents.manage")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    incident = await db.get(Incident, incident_id)
    if incident is None:
        raise NotFound("Incident", str(incident_id))
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        if value is not None:
            setattr(incident, field, value)
    if changes.get("status") == "resolved":
        incident.resolved_at = utcnow()
    await record_audit(
        db,
        action="incident.updated",
        resource_type="incident",
        resource_id=incident.id,
        actor=principal,
        request=request,
        metadata={"fields": sorted(changes)},
    )
    await db.commit()
    return {"id": incident.id, "status": incident.status, "resolved_at": incident.resolved_at}


@monitoring_router.get("/alerts", response_model=list[dict])
async def list_alerts(
    alert_status: str | None = None,
    application_id: uuid.UUID | None = None,
    limit: int = 50,
    _: Principal = Depends(require_platform("monitoring.read")),
    db: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    stmt = (
        select(MonitoringAlert, Application.name)
        .join(Application, Application.id == MonitoringAlert.application_id, isouter=True)
        .order_by(MonitoringAlert.last_triggered_at.desc())
        .limit(min(limit, 200))
    )
    if alert_status:
        stmt = stmt.where(MonitoringAlert.status == alert_status)
    if application_id:
        stmt = stmt.where(MonitoringAlert.application_id == application_id)
    rows = (await db.execute(stmt)).all()
    return [
        {
            "id": a.id,
            "name": a.name,
            "severity": a.severity,
            "status": a.status,
            "condition": a.condition,
            "application_id": a.application_id,
            "application_name": app_name,
            "last_triggered_at": a.last_triggered_at,
            "resolved_at": a.resolved_at,
        }
        for a, app_name in rows
    ]


@monitoring_router.post("/alerts/{alert_id}/resolve", response_model=dict)
async def resolve_alert(
    alert_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_platform("monitoring.manage")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    alert = await db.get(MonitoringAlert, alert_id)
    if alert is None:
        raise NotFound("Alert", str(alert_id))
    alert.status = "resolved"
    alert.resolved_at = utcnow()
    await record_audit(
        db,
        action="monitoring.alert_resolved",
        resource_type="monitoring_alert",
        resource_id=alert.id,
        actor=principal,
        request=request,
    )
    await db.commit()
    return {"id": alert.id, "status": alert.status}
