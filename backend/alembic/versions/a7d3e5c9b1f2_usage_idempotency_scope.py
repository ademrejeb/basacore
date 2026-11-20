"""Scope usage idempotency keys to the reporting application.

Revision ID: a7d3e5c9b1f2
Revises: f8a1c2d3e4b5
Create Date: 2026-11-15

``usage_records.idempotency_key`` was globally unique, so two applications that
happened to pick the same key collided: the SDK returned HTTP 409 or 500 for a
request that was perfectly valid inside its own app. Uniqueness now applies per
``application_id`` and only to non-null keys.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a7d3e5c9b1f2"
down_revision: str | None = "f8a1c2d3e4b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("uq_usage_records_idempotency_key", "usage_records", type_="unique")
    op.create_index(
        "uq_usage_records_application_idempotency",
        "usage_records",
        ["application_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_usage_records_application_idempotency", table_name="usage_records")
    op.create_unique_constraint(
        "uq_usage_records_idempotency_key", "usage_records", ["idempotency_key"]
    )
