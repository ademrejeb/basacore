# Database and tenant isolation

PostgreSQL 17 is the system of record. SQLAlchemy 2.1 models map to an Alembic-managed
schema; migrations also install the row-level security policies, the append-only audit
triggers and the runtime grants.

## Roles

| Role | Used by | Properties |
| --- | --- | --- |
| `platform_owner` | Alembic migrations only | Owns the schema; may create policies and triggers |
| `platform_app` | API runtime and seed | Login only, no `SUPERUSER`, no `BYPASSRLS`, not the table owner |

This split is the reason RLS is real: the API cannot bypass a policy by accident. The
bootstrap script `infra/postgres/init/01-roles.sh` creates the runtime role and default
privileges; migration `b3f1c9d2a7e4` grants DML on existing tables and creates the
policies.

## Entity groups

```
organizations ──< applications ──< application_environments
      │                │
      │                ├──< api_credentials
      │                ├──< application_clients ──< authorization_codes
      │                │                       └──< application_sessions
      │                ├──< application_configurations
      │                ├──< feature_flags ──< feature_flag_overrides
      │                └──< tenants ──< tenant_memberships
      │                         │      ├──< tenant_domains
      │                         │      ├──< tenant_configurations
      │                         │      ├──< tenant_resource_quotas
      │                         │      ├──< subscriptions ──< invoices ──< payments
      │                         │      └──< usage_records
      │                         └──< projects ──< tasks ──< task_comments
      │
      ├──< infrastructure_resources / deployments
      └──< incidents / monitoring_alerts

users ──< user_sessions
  │   ├──< authentication_events
  │   ├──< mfa_recovery_codes
  │   ├──< verification_tokens
  │   ├──< user_roles >── roles >── role_permissions >── permissions
  │   └──< notifications
```

`audit_logs` is intentionally not owned by a single aggregate. It carries `scope_type`
and `scope_id` (`platform`, `organization`, `application`, `tenant`) plus actor,
resource, outcome, request id, IP, user agent and JSON metadata.

## Row-level security map

Policies are defined in `backend/alembic/versions/b3f1c9d2a7e4_row_level_security.py`
and refined for audit inserts by `e5c8a1f4b2d7_audit_insert_policy.py`.

Helper functions:

```sql
app_current_tenant() RETURNS uuid
  -- nullif(current_setting('app.tenant_id', true), '')::uuid

app_rls_bypass() RETURNS boolean
  -- coalesce(current_setting('app.bypass_rls', true), 'off') = 'on'
```

| Table | Policy predicate |
| --- | --- |
| `tenants` | `app_rls_bypass() OR id = app_current_tenant()` |
| `tenant_memberships`, `tenant_domains`, `tenant_configurations`, `tenant_resource_quotas`, `feature_flag_overrides`, `usage_records`, `subscriptions` | `app_rls_bypass() OR tenant_id = app_current_tenant()` |
| `tasks`, `incidents`, `file_objects` | same, with nullable `tenant_id` (NULL rows are only visible through platform bypass) |
| `invoices` | bypass, or the parent subscription's `tenant_id` matches |
| `payments` | bypass, or the invoice → subscription chain matches |
| `audit_logs` (SELECT) | bypass, or `scope_type = 'tenant' AND scope_id = app_current_tenant()` |
| `audit_logs` (INSERT) | bypass, current tenant, or a `platform` / `organization` / `application` scope |

Tenant-scoped application queries run inside `tenant_scope(session, tenant_id)`; platform
queries run inside `platform_scope(session)`. Both helpers set the runtime variable,
commit the block, then commit the cleared value so a pooled connection cannot leak an
elevated context (`app/core/db.py`).

## Auth gateway tables

Migration `f8a1c2d3e4b5_auth_gateway.py` adds three tables:

| Table | Purpose | Key columns |
| --- | --- | --- |
| `application_clients` | OIDC login clients owned by an application | `client_id` (unique), `client_secret_hash`, `client_type`, `redirect_uris`, `post_logout_redirect_uris`, `allowed_origins`, `scopes`, `pkce_required`, `status` |
| `authorization_codes` | Short-lived, single-use authorization codes | `code_hash` (unique), client/application/user/tenant ids, `redirect_uri`, `scope`, `nonce`, `code_challenge`, `code_challenge_method`, `expires_at`, `consumed_at`, `auth_time`, `amr` |
| `application_sessions` | Refresh-token families for application logins | `refresh_token_hash` (unique), client/application/user/tenant ids, `scope`, `expires_at`, `revoked_at`, `revoked_reason`, `replaced_by_id` |

Redirect, scope and origin lists are JSONB arrays. `code_hash` and
`refresh_token_hash` are SHA-256 of opaque 32-byte values; the plaintext is never
stored or logged. Both hashes are indexed, and the session table has a composite
`(user_id, revoked_at, expires_at)` index for family revocation and cleanup.

### Why these tables are outside tenant RLS

They hold authentication state, not tenant business data, and the token endpoint has
no tenant context yet when it looks a row up. Every read is instead bound by an exact
tuple that the caller has already proved control of:

- a code is read by its secret hash, then its stored client, redirect URI, PKCE
  challenge, user and tenant are re-checked before tokens are issued;
- a session is read by its refresh-token hash, and the tenant and user are re-read
  before rotation.

The gateway never lists or searches these tables. Where it needs the named tenant row,
it calls `load_tenant` for that single id under a narrow `platform_scope`, never a
query across tenants. Consumers of the data (routes, admin UI) reach them through the
application scope with a permission check.

Tenant membership itself stays RLS-protected, so a valid user row plus a forged tenant
id is not enough to obtain access.

Codes and sessions are operational state: expired codes and revoked/expired sessions
can be purged by a scheduled job without affecting audit history.

## Append-only audit

`audit_logs` is protected at three layers:

1. `platform_app` does not hold `UPDATE`, `DELETE` or `TRUNCATE` (migration
   `c4a7e9f1d2b8_audit_append_only_privileges.py`).
2. Triggers `audit_logs_no_update_delete` and `audit_logs_no_truncate` raise for any
   mutation, including from the owner.
3. The API has no update or delete route for audit records.

Audit rows are written by `app/core/audit.py` with the acting principal, scope, outcome,
request id, IP and user agent. Denied and failed attempts are recorded as well.

## Migrations

```bash
cd backend
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\alembic.exe check        # must report no new operations
```

Migrations connect with `MIGRATION_DATABASE_URL` when set; otherwise they fall back to
`DATABASE_URL`. In containers the entrypoint runs `alembic upgrade head` as the owner
before the API starts. Tables created after the original grant migration explicitly
grant DML to `platform_app` in the same migration (see `f8a1c2d3e4b5`).

## Seeding

`python -m app.seed` seeds reference data (permissions, system roles, default security
policy) and a demo estate. `python -m app.seed --reference` seeds only the catalogue and
is safe to run on every boot. The demo seed is idempotent and checks for existing rows
by natural keys.

The demo estate includes the Atlas CRM application, the `northwind-sales` tenant, a
tenant user (`emma.wilson@northwind.dev`) and the OIDC login client
`basa_demo_atlas_web` with redirect `http://localhost:3003/auth/callback`. Its secret
is development-only and matches the demo application's configuration.

## Query conventions

- UUID primary keys generated by `gen_random_uuid()`.
- `timestamptz` everywhere; application timestamps come from `utcnow()`.
- Soft deletion uses `deleted_at` on tenants and similar aggregates; list endpoints
  exclude deleted rows.
- Enum columns are strings validated by Pydantic and services, keeping migrations cheap.
- Every list endpoint accepts `limit` and `offset` with bounded limits through
  `app/core/pagination.py`.
- Expensive list filters are server-side and parameterized; no user input is interpolated
  into SQL.

## Backup and restore

Use `pg_dump` in custom format, restore into a fresh database with the owner role, then
start the API so migrations and seeds can reconcile. The full runbook, including Redis
and key material, is in [OPERATIONS.md](OPERATIONS.md).
