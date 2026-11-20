from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class ProjectCreate(BaseModel):
    organization_id: uuid.UUID
    key: str
    name: str
    description: str | None = None
    lead_id: uuid.UUID | None = None


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    status: str | None = None
    lead_id: uuid.UUID | None = None


class TaskCreate(BaseModel):
    project_id: uuid.UUID
    parent_id: uuid.UUID | None = None
    title: str
    description: str | None = None
    status: str = "todo"
    priority: str = "medium"
    assignee_id: uuid.UUID | None = None
    due_date: str | None = None
    labels: list[str] = []
    application_id: uuid.UUID | None = None
    tenant_id: uuid.UUID | None = None
    incident_id: uuid.UUID | None = None


class TaskUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    status: str | None = None
    priority: str | None = None
    assignee_id: uuid.UUID | None = None
    due_date: str | None = None
    labels: list[str] | None = None


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    parent_id: uuid.UUID | None
    title: str
    description: str | None
    status: str
    priority: str
    assignee_id: uuid.UUID | None
    due_date: Any
    labels: list[str]
    application_id: uuid.UUID | None
    tenant_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class CommentCreate(BaseModel):
    body: str
