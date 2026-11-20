# Architecture

## Shape

Basacore is a modular monolith:

```
Browser ──▶ Next.js console ──▶ server-side proxy ──▶ FastAPI ──▶ PostgreSQL (RLS)
                                                   ├──▶ Redis (rate limits)
                                                   └──▶ SMTP (invitations)

Browser ──▶ external application (e.g. :3003) ──▶ authorization code + PKCE ──▶ FastAPI /v1/oauth/*
Application backend ──▶ OAuth client credentials ──▶ FastAPI /v1/app/*
```

There is one deployable API and one deployable web console. Modules communicate
through Python function calls and the shared session, not over the network. This is
deliberate: the YAGNI position is that a microservice split adds operational cost
before the domain boundaries are proven.

The demo application in `demo-app/` is not part of the product; it is an external
client that exercises the gateway exactly as a customer application would.

The knowledge-graph influence shows up in the authorization catalogue: permissions,
roles and scope types are a small, explicit graph seeded from one source of truth
(`app/core/permissions.py`), and every protected route names the permission it needs.
There is no implicit “admin can do everything” shortcut.

## Modules

| Module | Responsibility |
| --- | --- |
| `identity` | Users, passwords, JWT issue/verify, refresh rotation, MFA, sessions, auth events, invitations, password reset, platform user administration |
| `access` | Permission catalogue, roles, role grants, policy records |
| `organizations` | Company registry |
| `applications` | Application registry, environments, configuration versions, feature flags, API credentials, login clients, OAuth/OIDC gateway, `/v1/app/*` |
| `tenants` | Tenant lifecycle, memberships, domains, configuration history, quotas, tenant-scoped audit and usage |
| `billing` | Plans, subscriptions, invoices, payments, usage recording and report aggregation |
| `audit` | Read-side queries over the append-only trail |
| `dashboard` | Executive aggregates and recent activity |
| `infrastructure` | Resources, deployments, incidents, monitoring alerts |
| `projects` | Projects, tasks, comments, notifications, workflows (supporting domain) |

Each module owns its SQLAlchemy models, Pydantic schemas, service helpers and router.
Cross-module reads go through explicit queries or service functions.

## Request lifecycle

1. **Middleware** assigns a request id, enforces the body-size limit, applies security
   headers, and records structured logs. CORS is configured from explicit origins.
2. **Authentication** accepts either an httpOnly cookie (`basa_access`) or an
   `Authorization: Bearer` access token. Service tokens are accepted only by the
   integration API.
3. **Authorization** resolves the principal's platform permissions and scoped role
   grants. `require_platform(permission)` denies before any handler work.
4. **Tenant boundary** is not taken from a header. Platform operations that need
   cross-tenant reads enter `platform_scope` after authorization; tenant operations
   enter `tenant_scope(tenant.id)` before querying.
5. **Handler** runs service logic, records an audit event with actor, scope, outcome
   and request id, and commits.
6. **RLS context cleanup** commits the cleared `app.tenant_id` / `app.bypass_rls`
   setting before the pooled connection can be reused (`app/core/db.py`).

CSRF: cookie-authenticated unsafe requests must carry an `Origin`/`Referer` matching
`ALLOWED_ORIGINS`. The web console proxies API calls server-side and sets a trusted
`Origin`, so browser cookies never cross origins.

## Identity and authorization

- Passwords use Argon2id.
- Access tokens are RS256 JWTs with `iss`, `aud`, `sub`, `sid`, `jti`, `exp` and a
  permission snapshot. The public key is exposed through JWKS.
- Refresh tokens are opaque, stored hashed, rotated on every use, and reuse of a
  rotated token revokes the family.
- TOTP MFA uses `pyotp`; recovery codes are single-use and hashed at rest.
- Scopes are explicit grants: `(role, scope_type, scope_id)`. Effective permissions are
  the union of grants applicable to the current request.

## End-user auth gateway

Applications delegate sign-in to Basacore instead of implementing their own password
handling. The gateway is a first-party OIDC-style provider built from four components:

| Component | File | Responsibility |
| --- | --- | --- |
| Gateway service | `app/modules/applications/auth_service.py` | Client validation, tenant resolution and membership checks, effective roles, authorization-code lifecycle, application sessions, token issuance, userinfo, logout |
| Gateway router | `app/modules/applications/oauth_router.py` | `/v1/oauth/authorize`, `/token`, `/userinfo`, `/revoke`, `/logout`, embedded login/MFA, discovery documents |
| Token layer | `app/core/security.py` | `create_app_access_token`, `create_id_token`, `decode_app_token` |
| Data model | `app/modules/applications/models.py` | `application_clients`, `authorization_codes`, `application_sessions` |

### Hosted login

```
Browser            Application            Basacore
  │  click sign in     │                     │
  ├───────────────────▶│  302 /v1/oauth/authorize?client_id&redirect_uri&PKCE&tenant
  │                    ├────────────────────▶│  client + redirect + scope validated
  │                    │                     │  no console session → 303 /login?return_to
  │◀───────────────────┴─────────────────────┤
  │  sign in (password + MFA)                │
  ├──────────────────────────────────────────▶  POST /v1/auth/login, console cookie set
  │  303 /auth/continue?return_to=…          │
  ├──────────────────────────────────────────▶  console bridge → 303 back to authorize
  │                    │                     │  tenant membership verified, code issued
  │  302 redirect_uri?code&state             │
  │◀───────────────────┴─────────────────────┤
  │                    │  POST /token (secret + verifier)
  │                    ├────────────────────▶│  code consumed, app session created
  │                    │◀────────────────────┤  access + refresh + id_token
  │  200 app page      │                     │
```

The console's `/auth/continue` route is the only bridge between the web origin and the
API origin. It accepts a relative `/v1/oauth/authorize` path and resolves it against
`PUBLIC_API_BASE_URL`, so a tampered `return_to` cannot become an open redirect.

### Token and session model

- The console session stays a console session; the gateway issues its own
  `ApplicationSession` row and app-scoped tokens.
- App access tokens carry `typ=user_app`, `aud=<client_id>`, `sid`, `tenant_id`,
  `app_id`, roles and permissions. `userinfo` re-resolves `sid` on every call, so
  revocation is effective before token expiry.
- Refresh tokens are opaque, hashed and rotated; reuse revokes the family.
- Authorization codes are single-use and consumed under a row lock. A replay revokes
  the sessions created from the code.
- Roles and permissions are recomputed from stored grants at issuance and at userinfo;
  the application never supplies them.

### Tenant binding

The application supplies a `tenant` hint (slug or UUID). Basacore checks that the
client's application owns it, that it is `active`/`trial`, and that the user has an
active membership. With no hint, exactly one active membership is accepted; zero
returns `access_denied` and several return `tenant_required`. Internal staff may enter
any tenant of the application, never another application's tenant.

### Embedded login

Confidential clients may call `/v1/oauth/embedded/login` from their backend. It runs
the same identity checks as hosted login, optionally returns an MFA challenge, and
issues only application-scoped tokens. The secret and tokens stay server-side. Public
clients must use hosted login with PKCE.

## Tenant isolation

Enforcement is layered:

1. The application resolves the tenant from the authenticated principal or from the
   requested resource, never from a client-trusted tenant header.
2. Services enter `tenant_scope`, which sets `app.tenant_id`.
3. PostgreSQL RLS policies compare `tenant_id = app_current_tenant()`.
4. The runtime role is not the table owner and has `NOBYPASSRLS`.
5. Platform bypass is an explicit, transaction-local `app.bypass_rls` setting applied
   only after a platform authorization check.

See [DATABASE.md](DATABASE.md) for the table-by-table policy map.

## Web console

The console is a Next.js 16 App Router application:

- Pages are server components that call the API with the incoming cookies.
- Mutations are server actions in `web/app/actions.ts`; they call the API, validate
  errors, and `revalidatePath` the affected routes.
- Client components are limited to dialogs, forms, MFA enrollment and shell
  interactivity. They never receive API secrets.
- `/api/proxy/[...path]` exists for the login and MFA challenge flows where the browser
  must set cookies. It forwards cookies both ways and adds a trusted `Origin`.
- `@adem.rejeb/ui` supplies the design system; local formatting helpers keep dates and
  money consistent.

## Integration API

Applications authenticate with OAuth 2.0 client credentials at `/v1/oauth/token`.
Tokens are scoped to the credential and bound to one application. `/v1/app/*` then
exposes configuration, tenant resolution, identity summaries, authorization checks,
usage writes and audit writes. Cross-application tenant ids return `404`.

The TypeScript SDK in `packages/sdk-ts` caches tokens in memory, refreshes before
expiry, retries once on `401`, and throws structured errors.

End-user sign-in uses separate login clients on the same token endpoint. The two
credential types are intentionally distinct: an API credential cannot request a user
token, and a login client cannot call `/v1/app/*`.

## Deployment

```
             ┌────────────┐
  :3002 ───▶ │ web        │  Next.js standalone (console + hosted login)
             └─────┬──────┘
                   │ server-side fetch
             ┌─────▼──────┐        ┌─────────┐
  :8000 ───▶ │ api        │──────▶ │ postgres│  runtime role: platform_app
             └─────┬──────┘        └─────────┘
                   │
             ┌─────▼──────┐
             │ redis      │  rate limiting
             └────────────┘

  :3003 ───▶ demo-app     external Atlas CRM client (not part of the product)
                │  browser redirect + server-side token exchange
                └──────────────────────────────▶ api :8000 /v1/oauth/*
```

The API container runs migrations as the owner, then starts Uvicorn as a non-root user
with the runtime role. Configure shared JWT keys and `ENCRYPTION_KEY` through a secret
manager in managed environments.

## Deliberate non-goals

- Microservices, Kafka, event sourcing and a separate policy service are out of scope.
- Social identity providers (Google, Microsoft, GitHub) are not implemented. The OIDC
  gateway is first-party: customer applications consume it, but third parties cannot
  register clients or obtain consent screens.
- Only `shared_database` isolation is supported. The adapter point for schema- or
  database-per-tenant is documented in [ROADMAP.md](ROADMAP.md).
- Billing integrates through recorded invoices and payments; it is not a payment
  processor and does not claim PCI scope.
