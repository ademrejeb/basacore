from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record_audit
from app.core.db import get_session, utcnow
from app.core.deps import Principal, require_platform
from app.core.errors import NotFound, ValidationFailed
from app.core.pagination import PageParams, page_params, paginate
from app.modules.identity.models import User
from app.modules.projects.models import Notification, Project, Task, TaskComment
from app.modules.projects.schemas import (
    CommentCreate,
    ProjectCreate,
    ProjectUpdate,
    TaskCreate,
    TaskUpdate,
)

router = APIRouter(prefix="/projects", tags=["Internal operations"])
tasks_router = APIRouter(prefix="/tasks", tags=["Internal operations"])
notifications_router = APIRouter(prefix="/notifications", tags=["Internal operations"])


@router.get("", response_model=dict)
async def list_projects(
    params: PageParams = Depends(page_params),
    project_status: str | None = None,
    _: Principal = Depends(require_platform("projects.read")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    stmt = select(Project).order_by(Project.created_at.desc())
    if params.search:
        term = f"%{params.search.strip()}%"
        stmt = stmt.where(or_(Project.name.ilike(term), Project.key.ilike(term)))
    if project_status:
        stmt = stmt.where(Project.status == project_status)
    rows, total = await paginate(db, stmt, params)
    task_counts = dict(
        (
            await db.execute(
                select(Task.project_id, func.count())
                .where(Task.status.not_in(["done", "canceled"]))
                .group_by(Task.project_id)
            )
        ).all()
    )
    return {
        "items": [
            {
                "id": p.id,
                "key": p.key,
                "name": p.name,
                "description": p.description,
                "status": p.status,
                "lead_id": p.lead_id,
                "target_date": p.target_date,
                "open_tasks": task_counts.get(p.id, 0),
                "created_at": p.created_at,
            }
            for p in rows
        ],
        "total": total,
        "limit": params.limit,
        "offset": params.offset,
    }


@router.post("", response_model=dict, status_code=201)
async def create_project(
    payload: ProjectCreate,
    request: Request,
    principal: Principal = Depends(require_platform("projects.manage")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    from app.modules.organizations.models import Organization

    if await db.get(Organization, payload.organization_id) is None:
        raise NotFound("Organization", str(payload.organization_id))
    project = Project(
        organization_id=payload.organization_id,
        key=payload.key.upper(),
        name=payload.name,
        description=payload.description,
        lead_id=payload.lead_id,
        status="active",
    )
    db.add(project)
    await record_audit(
        db,
        action="project.created",
        resource_type="project",
        resource_id=project.id,
        actor=principal,
        request=request,
        metadata={"key": project.key},
    )
    await db.commit()
    return {"id": project.id, "key": project.key, "name": project.name}


@router.get("/{project_id}", response_model=dict)
async def get_project(
    project_id: uuid.UUID,
    _: Principal = Depends(require_platform("projects.read")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    project = await db.get(Project, project_id)
    if project is None:
        raise NotFound("Project", str(project_id))
    tasks = (
        (await db.execute(select(Task).where(Task.project_id == project.id).order_by(Task.created_at.desc())))
        .scalars()
        .all()
    )
    return {
        "id": project.id,
        "key": project.key,
        "name": project.name,
        "description": project.description,
        "status": project.status,
        "target_date": project.target_date,
        "tasks": [
            {
                "id": t.id,
                "title": t.title,
                "status": t.status,
                "priority": t.priority,
                "assignee_id": t.assignee_id,
                "due_date": t.due_date,
                "labels": t.labels,
                "application_id": t.application_id,
                "tenant_id": t.tenant_id,
            }
            for t in tasks
        ],
    }


@router.patch("/{project_id}", response_model=dict)
async def update_project(
    project_id: uuid.UUID,
    payload: ProjectUpdate,
    request: Request,
    principal: Principal = Depends(require_platform("projects.manage")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    project = await db.get(Project, project_id)
    if project is None:
        raise NotFound("Project", str(project_id))
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        if value is not None:
            setattr(project, field, value)
    await record_audit(
        db,
        action="project.updated",
        resource_type="project",
        resource_id=project.id,
        actor=principal,
        request=request,
        metadata={"fields": sorted(changes)},
    )
    await db.commit()
    return {"id": project.id, "status": project.status}


@tasks_router.get("", response_model=dict)
async def list_tasks(
    params: PageParams = Depends(page_params),
    project_id: uuid.UUID | None = None,
    task_status: str | None = None,
    assignee_id: uuid.UUID | None = None,
    application_id: uuid.UUID | None = None,
    tenant_id: uuid.UUID | None = None,
    _: Principal = Depends(require_platform("tasks.read")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    stmt = select(Task).order_by(Task.created_at.desc())
    if params.search:
        stmt = stmt.where(Task.title.ilike(f"%{params.search.strip()}%"))
    if project_id:
        stmt = stmt.where(Task.project_id == project_id)
    if task_status:
        stmt = stmt.where(Task.status == task_status)
    if assignee_id:
        stmt = stmt.where(Task.assignee_id == assignee_id)
    if application_id:
        stmt = stmt.where(Task.application_id == application_id)
    if tenant_id:
        stmt = stmt.where(Task.tenant_id == tenant_id)
    rows, total = await paginate(db, stmt, params)
    assignee_ids = {t.assignee_id for t in rows if t.assignee_id}
    names = dict(
        (
            await db.execute(
                select(User.id, User.full_name).where(User.id.in_(assignee_ids or [uuid.uuid4()]))
            )
        ).all()
    )
    return {
        "items": [
            {
                "id": t.id,
                "project_id": t.project_id,
                "title": t.title,
                "description": t.description,
                "status": t.status,
                "priority": t.priority,
                "assignee_id": t.assignee_id,
                "assignee_name": names.get(t.assignee_id),
                "due_date": t.due_date,
                "labels": t.labels,
                "application_id": t.application_id,
                "tenant_id": t.tenant_id,
                "created_at": t.created_at,
            }
            for t in rows
        ],
        "total": total,
        "limit": params.limit,
        "offset": params.offset,
    }


@tasks_router.post("", response_model=dict, status_code=201)
async def create_task(
    payload: TaskCreate,
    request: Request,
    principal: Principal = Depends(require_platform("tasks.manage")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    project = await db.get(Project, payload.project_id)
    if project is None:
        raise NotFound("Project", str(payload.project_id))
    due: date | None = None
    if payload.due_date:
        try:
            due = date.fromisoformat(payload.due_date)
        except ValueError as exc:
            raise ValidationFailed("due_date must be an ISO date (YYYY-MM-DD).") from exc
    task = Task(
        project_id=project.id,
        parent_id=payload.parent_id,
        title=payload.title,
        description=payload.description,
        status=payload.status,
        priority=payload.priority,
        assignee_id=payload.assignee_id,
        reporter_id=principal.user.id,
        due_date=due,
        labels=payload.labels,
        application_id=payload.application_id,
        tenant_id=payload.tenant_id,
        incident_id=payload.incident_id,
    )
    db.add(task)
    if payload.assignee_id and payload.assignee_id != principal.user.id:
        db.add(
            Notification(
                user_id=payload.assignee_id,
                title="Task assigned",
                body=f"{principal.user.full_name} assigned you: {task.title}",
                category="task",
                link=f"/tasks/{task.id}",
                created_at=utcnow(),
            )
        )
    await record_audit(
        db,
        action="task.created",
        resource_type="task",
        resource_id=task.id,
        actor=principal,
        request=request,
        metadata={"project_id": str(project.id), "title": task.title},
    )
    await db.commit()
    return {"id": task.id, "title": task.title, "status": task.status}


@tasks_router.get("/{task_id}", response_model=dict)
async def get_task(
    task_id: uuid.UUID,
    _: Principal = Depends(require_platform("tasks.read")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    task = await db.get(Task, task_id)
    if task is None:
        raise NotFound("Task", str(task_id))
    comments = (
        await db.execute(
            select(TaskComment, User)
            .join(User, User.id == TaskComment.author_id, isouter=True)
            .where(TaskComment.task_id == task.id)
            .order_by(TaskComment.created_at)
        )
    ).all()
    return {
        "id": task.id,
        "project_id": task.project_id,
        "title": task.title,
        "description": task.description,
        "status": task.status,
        "priority": task.priority,
        "assignee_id": task.assignee_id,
        "due_date": task.due_date,
        "labels": task.labels,
        "application_id": task.application_id,
        "tenant_id": task.tenant_id,
        "incident_id": task.incident_id,
        "created_at": task.created_at,
        "comments": [
            {
                "id": c.id,
                "body": c.body,
                "author_id": c.author_id,
                "author_name": u.full_name if u else None,
                "created_at": c.created_at,
            }
            for c, u in comments
        ],
    }


@tasks_router.patch("/{task_id}", response_model=dict)
async def update_task(
    task_id: uuid.UUID,
    payload: TaskUpdate,
    request: Request,
    principal: Principal = Depends(require_platform("tasks.manage")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    task = await db.get(Task, task_id)
    if task is None:
        raise NotFound("Task", str(task_id))
    changes = payload.model_dump(exclude_unset=True)
    if "due_date" in changes and changes["due_date"]:
        changes["due_date"] = date.fromisoformat(changes["due_date"])
    for field, value in changes.items():
        if value is not None:
            setattr(task, field, value)
    if changes.get("status") in ("done", "canceled"):
        task.completed_at = utcnow()
    await record_audit(
        db,
        action="task.updated",
        resource_type="task",
        resource_id=task.id,
        actor=principal,
        request=request,
        metadata={"fields": sorted(changes)},
    )
    await db.commit()
    return {"id": task.id, "status": task.status, "priority": task.priority}


@tasks_router.post("/{task_id}/comments", response_model=dict, status_code=201)
async def add_comment(
    task_id: uuid.UUID,
    payload: CommentCreate,
    request: Request,
    principal: Principal = Depends(require_platform("tasks.manage")),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    task = await db.get(Task, task_id)
    if task is None:
        raise NotFound("Task", str(task_id))
    comment = TaskComment(
        task_id=task.id,
        author_id=principal.user.id,
        body=payload.body,
        created_at=utcnow(),
    )
    db.add(comment)
    await record_audit(
        db,
        action="task.comment_added",
        resource_type="task",
        resource_id=task.id,
        actor=principal,
        request=request,
    )
    await db.commit()
    return {"id": comment.id, "created_at": comment.created_at}


@notifications_router.get("", response_model=list[dict])
async def list_notifications(
    limit: int = 30,
    principal: Principal = Depends(require_platform("notifications.read")),
    db: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    rows = (
        (
            await db.execute(
                select(Notification)
                .where(Notification.user_id == principal.user.id)
                .order_by(Notification.created_at.desc())
                .limit(min(limit, 100))
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": n.id,
            "title": n.title,
            "body": n.body,
            "category": n.category,
            "link": n.link,
            "read_at": n.read_at,
            "created_at": n.created_at,
        }
        for n in rows
    ]


@notifications_router.post("/{notification_id}/read", status_code=204)
async def mark_read(
    notification_id: uuid.UUID,
    principal: Principal = Depends(require_platform("notifications.read")),
    db: AsyncSession = Depends(get_session),
) -> None:
    notification = await db.get(Notification, notification_id)
    if notification is None or notification.user_id != principal.user.id:
        raise NotFound("Notification", str(notification_id))
    notification.read_at = utcnow()
    await db.commit()
