"""Stage MFA replacement secrets and add password history.

Revision ID: b9c4d2e1f7a3
Revises: a7d3e5c9b1f2
Create Date: 2026-11-15

Re-enrolling MFA used to overwrite the active secret and set ``mfa_enabled`` to
false before the new code was proven. The pending columns keep the active
factor valid until confirmation. Password history makes the configurable
``password_history`` policy enforceable.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b9c4d2e1f7a3"
down_revision: str | None = "a7d3e5c9b1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("mfa_pending_secret_encrypted", sa.Text(), nullable=True))
    op.add_column(
        "users", sa.Column("mfa_pending_started_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_table(
        "password_history",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_password_history_user_created", "password_history", ["user_id", "created_at"]
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON password_history TO platform_app"
    )


def downgrade() -> None:
    op.drop_index("ix_password_history_user_created", table_name="password_history")
    op.drop_table("password_history")
    op.drop_column("users", "mfa_pending_started_at")
    op.drop_column("users", "mfa_pending_secret_encrypted")
