"""Split audit RLS into read and append policies.

Revision ID: e5c8a1f4b2d7
Revises: b3f1c9d2a7e4
Create Date: 2026-11-02

Application-scoped services (for example the OAuth client-credentials grant)
must append audit records without being granted tenant read visibility. A
single permissive policy could not express both rules, so reads remain narrow
(platform bypass or the current tenant) while appends additionally accept
non-tenant scopes. The append-only triggers still reject every update, delete
and truncate.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "e5c8a1f4b2d7"
down_revision: str | None = "b3f1c9d2a7e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DROP POLICY IF EXISTS audit_logs_isolation ON audit_logs")
    op.execute(
        """
        CREATE POLICY audit_logs_select ON audit_logs
        FOR SELECT
        USING (
            app_rls_bypass()
            OR (scope_type = 'tenant' AND scope_id = app_current_tenant())
        )
        """
    )
    op.execute(
        """
        CREATE POLICY audit_logs_insert ON audit_logs
        FOR INSERT
        WITH CHECK (
            app_rls_bypass()
            OR (scope_type = 'tenant' AND scope_id = app_current_tenant())
            OR scope_type IN ('platform', 'organization', 'application')
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS audit_logs_insert ON audit_logs")
    op.execute("DROP POLICY IF EXISTS audit_logs_select ON audit_logs")
    op.execute(
        """
        CREATE POLICY audit_logs_isolation ON audit_logs
        USING (
            app_rls_bypass()
            OR (scope_type = 'tenant' AND scope_id = app_current_tenant())
        )
        WITH CHECK (
            app_rls_bypass()
            OR (scope_type = 'tenant' AND scope_id = app_current_tenant())
        )
        """
    )
