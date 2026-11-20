"""Make audit_logs append-only at the privilege level

The RLS policy split in ``e5c8a1f4b2d7`` intentionally leaves UPDATE and DELETE
without an applicable policy, so those statements currently match zero rows.
That is safe but silent. Revoking the privileges outright makes any attempt fail
loudly, and keeps the append-only trigger as defense in depth for the owner role.

Revision ID: c4a7e9f1d2b8
Revises: e5c8a1f4b2d7
Create Date: 2026-11-04
"""

from __future__ import annotations

from alembic import op

revision: str = "c4a7e9f1d2b8"
down_revision: str | None = "e5c8a1f4b2d7"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON audit_logs FROM platform_app")


def downgrade() -> None:
    op.execute("GRANT UPDATE, DELETE ON audit_logs TO platform_app")
