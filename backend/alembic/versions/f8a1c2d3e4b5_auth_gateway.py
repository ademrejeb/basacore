"""End-user auth gateway: login clients, authorization codes, app sessions.

Revision ID: f8a1c2d3e4b5
Revises: c4a7e9f1d2b8
Create Date: 2026-11-11

These tables carry authentication state, not tenant business data. They are
intentionally outside the tenant RLS policies: every read is bound to a hashed
secret or an exact client/user/tenant tuple, and the gateway only ever resolves
one tenant context at a time. Tenant membership itself remains RLS-protected.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f8a1c2d3e4b5"
down_revision: str | None = "c4a7e9f1d2b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "application_clients",
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("client_id", sa.String(length=64), nullable=False),
        sa.Column("client_secret_hash", sa.String(length=64), nullable=True),
        sa.Column("client_type", sa.String(length=20), nullable=False),
        sa.Column("redirect_uris", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "post_logout_redirect_uris", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("allowed_origins", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("scopes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("pkce_required", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["application_id"], ["applications.id"], name=op.f("fk_application_clients_application_id_applications"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_application_clients_created_by_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_application_clients")),
    )
    op.create_index(
        op.f("ix_application_clients_application_id"), "application_clients", ["application_id"], unique=False
    )
    op.create_index(op.f("ix_application_clients_client_id"), "application_clients", ["client_id"], unique=True)
    op.create_index(op.f("ix_application_clients_status"), "application_clients", ["status"], unique=False)

    op.create_table(
        "authorization_codes",
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("application_client_id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("redirect_uri", sa.String(length=500), nullable=False),
        sa.Column("scope", sa.String(length=500), nullable=False),
        sa.Column("nonce", sa.String(length=255), nullable=True),
        sa.Column("code_challenge", sa.String(length=128), nullable=True),
        sa.Column("code_challenge_method", sa.String(length=10), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("auth_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("amr", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["application_client_id"],
            ["application_clients.id"],
            name=op.f("fk_authorization_codes_application_client_id_application_clients"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["application_id"], ["applications.id"], name=op.f("fk_authorization_codes_application_id_applications"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_authorization_codes_user_id_users"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_authorization_codes_tenant_id_tenants"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["user_sessions.id"], name=op.f("fk_authorization_codes_session_id_user_sessions"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_authorization_codes")),
    )
    op.create_index(op.f("ix_authorization_codes_code_hash"), "authorization_codes", ["code_hash"], unique=True)
    op.create_index(
        op.f("ix_authorization_codes_application_client_id"),
        "authorization_codes",
        ["application_client_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_authorization_codes_application_id"), "authorization_codes", ["application_id"], unique=False
    )
    op.create_index(op.f("ix_authorization_codes_user_id"), "authorization_codes", ["user_id"], unique=False)
    op.create_index(op.f("ix_authorization_codes_tenant_id"), "authorization_codes", ["tenant_id"], unique=False)
    op.create_index("ix_auth_codes_expiry", "authorization_codes", ["expires_at"], unique=False)

    op.create_table(
        "application_sessions",
        sa.Column("application_client_id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("refresh_token_hash", sa.String(length=64), nullable=False),
        sa.Column("scope", sa.String(length=500), nullable=False),
        sa.Column("nonce", sa.String(length=255), nullable=True),
        sa.Column("user_agent", sa.String(length=500), nullable=True),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("auth_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("amr", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_reason", sa.String(length=120), nullable=True),
        sa.Column("replaced_by_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["application_client_id"],
            ["application_clients.id"],
            name=op.f("fk_application_sessions_application_client_id_application_clients"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["application_id"], ["applications.id"], name=op.f("fk_application_sessions_application_id_applications"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_application_sessions_user_id_users"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_application_sessions_tenant_id_tenants"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["replaced_by_id"], ["application_sessions.id"], name=op.f("fk_application_sessions_replaced_by_id_application_sessions")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_application_sessions")),
    )
    op.create_index(
        op.f("ix_application_sessions_application_client_id"),
        "application_sessions",
        ["application_client_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_application_sessions_application_id"), "application_sessions", ["application_id"], unique=False
    )
    op.create_index(op.f("ix_application_sessions_user_id"), "application_sessions", ["user_id"], unique=False)
    op.create_index(op.f("ix_application_sessions_tenant_id"), "application_sessions", ["tenant_id"], unique=False)
    op.create_index(
        op.f("ix_application_sessions_refresh_token_hash"),
        "application_sessions",
        ["refresh_token_hash"],
        unique=True,
    )
    op.create_index(
        "ix_app_sessions_user_active", "application_sessions", ["user_id", "revoked_at", "expires_at"], unique=False
    )

    # The runtime role cannot rely on default privileges for tables created after
    # the original grant migration.
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON application_clients, authorization_codes, "
        "application_sessions TO platform_app"
    )


def downgrade() -> None:
    op.drop_table("application_sessions")
    op.drop_table("authorization_codes")
    op.drop_table("application_clients")
