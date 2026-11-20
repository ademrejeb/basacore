# Roadmap and extension points

Basacore follows a YAGNI discipline: a capability is built when an operator needs it and
when it can be enforced server-side with tests. The platform is deliberately a modular
monolith. The roadmap is therefore a list of **extension points**, not a promise of
features. Each entry states what exists now, where the seam is, and the trigger for
building it.

## 1. What this release deliberately includes

| Area | Implemented |
| --- | --- |
| Registry | Organizations, applications, environments, configuration versions, feature flags, credentials, webhook and integration records |
| Tenancy | Provisioning, lifecycle states, domains, memberships, quotas, versioned tenant configuration |
| Identity | First-party login, Argon2id, RS256 JWT/JWKS, refresh rotation and reuse detection, TOTP MFA, recovery codes, sessions, invitations, reset |
| Authorization | 80 permissions, 18 system roles, platform/organization/application/tenant scopes, custom roles, server-side checks |
| Isolation | Shared database with PostgreSQL RLS, non-owner runtime role, explicit platform bypass |
| Billing | Plans, subscriptions, invoices, payments, MRR/ARR, metered usage, quota enforcement |
| Operations | Infrastructure resources, deployments, incidents, monitoring alerts, dashboard |
| Assurance | Append-only audit, authentication events, request ids |
| Integration | OAuth 2.0 client credentials, `/v1/app/*`, TypeScript SDK |
| End-user auth | First-party OIDC-style gateway: authorization code + PKCE, hosted and embedded login, ID tokens, userinfo, app sessions, tenant membership verification |
| Console | Next.js admin UI for every workflow above |

## 2. Deliberate deferrals

| Capability | Current state | Extension point | Build when |
| --- | --- | --- | --- |
| External identity federation (social / SAML) | First-party OIDC gateway exists for customer apps; no external IdP adapter or SAML | `app/modules/identity/` service and router; add a provider adapter and an identity-link table | A customer requires enterprise SSO or social sign-in |
| Webhook delivery and retries | Records and secrets exist; no scheduler or delivery worker | `Webhook` model, `application_configurations`, audit events, Redis | An integration needs push notifications |
| Payment provider integration | Invoices and payments are recorded internally; no PCI scope | `app/modules/billing/` service; provider webhook route with signature verification | Billing must collect money |
| Schema- or database-per-tenant | Only `shared_database` is accepted | `ENABLED_ISOLATION_STRATEGIES` in `app/modules/tenants/service.py`, tenant provisioning, RLS helpers | A tenant contract requires physical isolation |
| SCIM provisioning | Not implemented | New module beside `identity`; map to users, memberships and roles | An enterprise buyer mandates automated user lifecycle |
| Email delivery reliability | SMTP is real; development falls back to a log sink; no retry queue | `app/core/mailer.py` | Invitations and resets must survive SMTP outages at scale |
| Password history enforcement | `password_history` is configurable, but only the current password is rejected; previous hashes are not stored | `app/modules/identity/service.py`; add a `password_history` table keyed by user and check it on set | A reuse policy is contractually required |
| PITR / WAL archiving | Logical dumps only; runbook documents the gap | PostgreSQL deployment layer, not application code | RPO below 24 hours is required |
| Horizontal API scaling | Stateless API, shared Redis, per-replica DB pool | Deployment layer; no code change expected | Single-replica throughput is insufficient |
| Metrics and tracing | Structured JSON logs, request ids, health endpoints | `RequestContextMiddleware`, OpenTelemetry dependency already present transitively | Operators need SLO dashboards and traces |
| Knowledge-graph exploration | The authorization catalogue is a small explicit graph; no graph UI | `app/core/permissions.py` and access module | Operators need to answer cross-entity impact questions |

### On microservices

Do not split the monolith to follow fashion. A split is justified only when a module has
a genuinely different scaling, failure or compliance profile. The current module
boundaries already make that extraction possible later: each module owns its models,
schemas, service functions and router.

## 3. Extension: tenant isolation adapters

The current enforcement path is:

```
principal → tenant_scope(tenant_id) → SET app.tenant_id → RLS policy → row filter
```

Only `IsolationStrategy.SHARED` is enabled (`ENABLED_ISOLATION_STRATEGIES`). Other
strategies are rejected at provisioning time with a documented validation error rather
than silently pretending to isolate.

To add a schema-per-tenant or database-per-tenant strategy:

1. Add the enum value in `app/core/enums.py`.
2. Add a provisioning adapter that creates the schema/database, applies migrations, and
   stores the connection reference or schema name on the tenant.
3. Resolve the connection/schema in `app/core/db.py` when entering `tenant_scope`; keep
   `app.tenant_id` set as defence in depth.
4. Keep the platform bypass path read-only-by-policy and audited.
5. Extend `backend/tests/test_isolation.py` to run the same cross-tenant attempts against
   the new strategy; the suite must fail if isolation regresses.
6. Document backup/restore per strategy in `docs/OPERATIONS.md`.

Do not enable an enum value without the provisioning, backup and test paths. A strategy
that only changes a label is a security bug.

## 4. Extension: webhook delivery

The database already stores webhook URLs, encrypted signing secrets, subscribed events,
activation state, last delivery and failure count. What is missing is delivery:

1. Add an outbox table or reuse `audit_logs` as the event source with a cursor.
2. Add a worker process (the API image can run a second command) that claims pending
   deliveries with `FOR UPDATE SKIP LOCKED`.
3. Sign the payload with the stored secret, use exponential backoff, cap attempts, and
   record every attempt in the audit trail.
4. Add SSRF protection: allow only HTTPS, resolve and reject private/link-local ranges,
   and do not follow redirects to them.
5. Add per-application rate limits in Redis.
6. Test signature verification, retry, dead-letter and duplicate delivery.

Webhook secrets are already encrypted with `ENCRYPTION_KEY`; the worker must decrypt
only at send time and never log the secret or the signature key.

## 5. Extension: external identity federation

Basacore is already an OIDC provider for first-party customer applications: the
authorization-code + PKCE flow, ID tokens, userinfo, hosted/embedded login and
application sessions are implemented and tested. What is not built is federation with
an **external** identity provider (Google, Microsoft Entra, Okta, SAML). If that is
added:

1. Add a provider adapter (authorization-code client) in a dedicated module; keep the
   existing `/v1/oauth/*` gateway as the issuer for customer applications.
2. Validate `state`, `nonce`, issuer, audience, signature and clock skew against the
   provider's JWKS.
3. Map the external subject to a local user through a new identity-link table; never
   auto-create a platform administrator from an unverified email.
4. Keep local MFA and session issuance as the final step, so the audit trail remains
   uniform.
5. Encrypt provider client secrets at rest and expose configuration through the
   application or platform settings.
6. Test discovery failure, nonce mismatch, expired code, wrong audience and account
   linking collisions.

The existing first-party login, gateway tokens, refresh rotation and session model stay
the source of truth for authorization.

## 6. Extension: payment provider

The billing module records plans, subscriptions, invoices and payments. It is not a
payment processor.

1. Add a provider adapter interface (create customer, create checkout session, refund)
   with one concrete implementation.
2. Add a signed webhook endpoint that is idempotent by provider event id.
3. Reconcile provider events into `payments` and `invoices` inside the same transaction
   that writes the audit row.
4. Never store card data; store provider references only.
5. Test signature failure, replay, partial refund and out-of-order events.

## 7. Extension: a new domain module

Use the existing shape:

```
app/modules/<name>/
  models.py     SQLAlchemy models; tenant-scoped tables need a tenant_id and RLS
  schemas.py    Pydantic input/output models with bounded fields
  service.py    Business rules, quota checks, audit writes
  router.py     Thin HTTP layer; every route names a permission
```

Checklist:

- Add the permission to `app/core/permissions.py` and grant it to the relevant roles.
- Use `require_platform(...)` for platform routes; use `tenant_scope` plus
  `authorize_tenant` for tenant routes.
- Add RLS policies and grants in an Alembic migration if the table is tenant-scoped.
- Record audit events for state changes, denials and security-relevant reads.
- Add tests for success, permission denial, cross-tenant access and quota limits.
- Update `docs/ARCHITECTURE.md`, `docs/DATABASE.md` and `docs/SECURITY.md`.
- Run `alembic check`; a model change without a migration is not done.

## 8. Non-negotiable invariants

Any future increment must preserve these:

1. **Server-side authorization** on every protected operation. UI hiding is not
   enforcement.
2. **No client-trusted tenant or auth context.** Tenant identity comes from the
   principal or the resource, then is verified.
3. **RLS stays real.** The runtime role remains a non-owner with `NOBYPASSRLS`; platform
   bypass is explicit, authorized and transaction-local.
4. **Audit stays append-only.** No update/delete route, privilege or trigger escape
   hatch.
5. **No plaintext secrets.** Hash what can be hashed; encrypt what must be recovered;
   reveal once.
6. **No fake production features.** A capability is either implemented and tested or
   documented as deferred, as in this file.
7. **Migrations are reviewed.** `alembic check` must be clean; destructive changes need a
   backup and a rollback plan.
8. **Dependency versions stay pinned.** The UI package peer constraint keeps
   `lucide-react` below `1.0.0`.

## 9. Definition of done for a future increment

- Behaviour is enforced on the server and covered by a test that fails without the
  change.
- A cross-tenant or unauthorized attempt is explicitly tested.
- Audit, error envelope, pagination and rate limiting follow existing conventions.
- `pytest`, `ruff check`, `ruff format --check`, `alembic check`, `npm run typecheck` and
  `npm run build` pass.
- Documentation and the acceptance-scenario map are updated.
- The Compose stack still starts with one command and reports healthy.
