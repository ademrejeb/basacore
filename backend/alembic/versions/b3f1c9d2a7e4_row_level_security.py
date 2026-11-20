"""Row-level security, append-only audit, and runtime grants.

Revision ID: b3f1c9d2a7e4
Revises: 27060b6087a0
Create Date: 2026-11-02

The runtime role (platform_app) is neither the table owner nor BYPASSRLS, so
these policies are enforced for every API query. Platform operations opt in to
bypass explicitly with app.bypass_rls after an authorization check.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "b3f1c9d2a7e4"
down_revision: str | None = "27060b6087a0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


DIRECT_TENANT_TABLES = (
    "tenant_memberships",
    "tenant_domains",
    "tenant_configurations",
    "tenant_resource_quotas",
    "feature_flag_overrides",
    "usage_records",
    "subscriptions",
)

NULLABLE_TENANT_TABLES = ("tasks", "incidents", "file_objects")


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_current_tenant() RETURNS uuid
        LANGUAGE sql STABLE AS $$
            SELECT nullif(current_setting('app.tenant_id', true), '')::uuid
        $$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app_rls_bypass() RETURNS boolean
        LANGUAGE sql STABLE AS $$
            SELECT coalesce(current_setting('app.bypass_rls', true), 'off') = 'on'
        $$;
        """
    )

    op.execute("ALTER TABLE tenants ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenants_isolation ON tenants
        USING (app_rls_bypass() OR id = app_current_tenant())
        WITH CHECK (app_rls_bypass() OR id = app_current_tenant())
        """
    )

    for table in DIRECT_TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_isolation ON {table}
            USING (app_rls_bypass() OR tenant_id = app_current_tenant())
            WITH CHECK (app_rls_bypass() OR tenant_id = app_current_tenant())
            """
        )

    for table in NULLABLE_TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_isolation ON {table}
            USING (app_rls_bypass() OR tenant_id = app_current_tenant())
            WITH CHECK (app_rls_bypass() OR tenant_id = app_current_tenant())
            """
        )

    op.execute("ALTER TABLE invoices ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY invoices_isolation ON invoices
        USING (
            app_rls_bypass()
            OR EXISTS (
                SELECT 1 FROM subscriptions s
                WHERE s.id = invoices.subscription_id
                  AND s.tenant_id = app_current_tenant()
            )
        )
        WITH CHECK (
            app_rls_bypass()
            OR EXISTS (
                SELECT 1 FROM subscriptions s
                WHERE s.id = invoices.subscription_id
                  AND s.tenant_id = app_current_tenant()
            )
        )
        """
    )

    op.execute("ALTER TABLE payments ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY payments_isolation ON payments
        USING (
            app_rls_bypass()
            OR EXISTS (
                SELECT 1
                FROM invoices i
                JOIN subscriptions s ON s.id = i.subscription_id
                WHERE i.id = payments.invoice_id
                  AND s.tenant_id = app_current_tenant()
            )
        )
        WITH CHECK (
            app_rls_bypass()
            OR EXISTS (
                SELECT 1
                FROM invoices i
                JOIN subscriptions s ON s.id = i.subscription_id
                WHERE i.id = payments.invoice_id
                  AND s.tenant_id = app_current_tenant()
            )
        )
        """
    )

    op.execute("ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY")
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

    op.execute(
        """
        CREATE OR REPLACE FUNCTION reject_audit_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_logs is append-only; % is not permitted', TG_OP;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_logs_no_update_delete
        BEFORE UPDATE OR DELETE ON audit_logs
        FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation()
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_logs_no_truncate
        BEFORE TRUNCATE ON audit_logs
        FOR EACH STATEMENT EXECUTE FUNCTION reject_audit_mutation()
        """
    )

    # The runtime role needs explicit DML grants. Default privileges cover new
    # tables created by the owner, but stating them here keeps the contract
    # visible and makes a fresh restore deterministic.
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO platform_app"
    )
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO platform_app")
    op.execute("GRANT EXECUTE ON FUNCTION app_current_tenant() TO platform_app")
    op.execute("GRANT EXECUTE ON FUNCTION app_rls_bypass() TO platform_app")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_logs_no_truncate ON audit_logs")
    op.execute("DROP TRIGGER IF EXISTS audit_logs_no_update_delete ON audit_logs")
    op.execute("DROP FUNCTION IF EXISTS reject_audit_mutation()")
    op.execute("DROP POLICY IF EXISTS audit_logs_isolation ON audit_logs")
    op.execute("ALTER TABLE audit_logs DISABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS payments_isolation ON payments")
    op.execute("ALTER TABLE payments DISABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS invoices_isolation ON invoices")
    op.execute("ALTER TABLE invoices DISABLE ROW LEVEL SECURITY")
    for table in NULLABLE_TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    for table in DIRECT_TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenants_isolation ON tenants")
    op.execute("ALTER TABLE tenants DISABLE ROW LEVEL SECURITY")
    op.execute("DROP FUNCTION IF EXISTS app_rls_bypass()")
    op.execute("DROP FUNCTION IF EXISTS app_current_tenant()")
