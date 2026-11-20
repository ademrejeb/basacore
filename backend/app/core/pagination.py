from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

T = TypeVar("T")


@dataclass
class PageParams:
    limit: int = 50
    offset: int = 0
    search: str | None = None


def page_params(
    limit: int = Query(default=50, ge=1, le=200, description="Maximum rows to return"),
    offset: int = Query(default=0, ge=0, description="Rows to skip"),
    search: str | None = Query(default=None, max_length=200, description="Free-text search"),
) -> PageParams:
    return PageParams(limit=limit, offset=offset, search=search)


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


async def paginate(db: AsyncSession, stmt: Select, params: PageParams) -> tuple[list[Any], int]:
    count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
    total = int((await db.execute(count_stmt)).scalar_one())
    rows = (await db.execute(stmt.limit(params.limit).offset(params.offset))).scalars().all()
    return list(rows), total


def build_page(items: list[Any], total: int, params: PageParams) -> dict[str, Any]:
    return {"items": items, "total": total, "limit": params.limit, "offset": params.offset}


def ensure_uuid(value: str, field: str = "id") -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        from app.core.errors import AppError

        raise AppError("invalid_id", f"{field} must be a valid UUID.", 422) from exc
