# Basacore

Basacore is a centralized application, tenant and company operating platform, and the
identity gateway for the applications it runs. It is a modular monolith with a FastAPI
API, a Next.js control plane, PostgreSQL with row-level security, Redis, and a typed
integration SDK.

It is designed for teams that operate many customer-facing applications and need one
place to register applications, onboard organizations and tenants, centralize
identity and authorization, enforce quotas, bill subscriptions, observe operations,
and prove what happened through an append-only audit trail.

Customer applications delegate end-user sign-in to Basacore. After login the app
receives a verified identity, the tenant the user is entering, and that user's roles
and permissions. Hosted login and embedded server-side login are both supported, and
tenant context supplied by the app is always checked against real membership.

## Quick start

Requirements: Docker 29+, Docker Compose v2. Nothing else is needed for the
containerized path.

```bash
docker compose up --build
```

Then open:

| Service | URL |
| --- | --- |
| Admin console | http://localhost:3002 |
| API | http://localhost:8000 |
| OpenAPI docs | http://localhost:8000/docs |
| Health | http://localhost:8000/healthz |
| Readiness | http://localhost:8000/readyz |
| Demo external app | http://localhost:3003 |

The API container applies migrations, syncs reference data, and seeds a demo estate
on first start. Set `SEED_DEMO=false` to seed only permissions, roles and policy.

Demo administrator (development only — change it anywhere else):

```
admin@basa.dev
Adm1n!Basa#2026
```

The demo tenant users use the same password. The seeded SDK credential is printed at
the end of the API container logs:

```bash
docker compose logs api | Select-String "SDK client"
```

### End-user sign-in quickstart

The Compose stack also starts `demo-app/`, a small external Atlas CRM client on
`http://localhost:3003`. It is not part of Basacore; it behaves like a customer
application.

1. Open http://localhost:3003 and choose **Sign in with Basacore**.
2. Basacore's hosted login opens on port 3002. Sign in as
   `emma.wilson@northwind.dev` / `Adm1n!Basa#2026`.
3. The tenant hint `northwind-sales` is verified against the user's membership, then
   the app receives a code, exchanges it server-side, verifies the ID token against
   the JWKS and displays the tenant, roles and permissions from userinfo.

The app also demonstrates embedded server-side login and single logout. Seeded login
client:

```text
client_id      basa_demo_atlas_web
client_secret  bsc_demo_atlas_web_secret_2026
redirect_uri   http://localhost:3003/auth/callback
tenant         northwind-sales
```

These values are development-only. Create real login clients in the console under
**Applications → Login clients**. See [docs/INTEGRATION.md](docs/INTEGRATION.md) for
the full hosted and embedded flows.

### Local development without containers

Start PostgreSQL and Redis with `docker compose up -d db redis`, then:

```bash
cd backend
python -m venv .venv
.\.venv\Scripts\pip install -e ".[dev]"
.\.venv\Scripts\alembic upgrade head
.\.venv\Scripts\python -m app.seed
.\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

cd ..\web
npm ci
npm run dev
```

Create a local `.env` from `.env.example` before either path. The development
defaults target PostgreSQL on `5434`, Redis on `6380`, the API on `8000`, the
web console on `3002` and the demo external app on `3003`.

To run the demo app without Docker after the API and console are up:

```bash
cd demo-app
npm start
```

It reads `BASACORE_API_BASE_URL`, `PUBLIC_BASE_URL`, `BASACORE_CLIENT_ID`,
`BASACORE_CLIENT_SECRET` and `BASACORE_TENANT`, with localhost defaults from
`.env.example`. In Compose, server-side calls use `BASACORE_INTERNAL_API_BASE_URL`
(`http://api:8000`) while browser redirects use the public URL.

## What is included

| Area | Capabilities |
| --- | --- |
| Registry | Organizations, applications, environments, configuration versions, feature flags, API credentials, OIDC login clients |
| Tenancy | Tenant provisioning, lifecycle (trial, active, suspended, archived, deleted), domains, users, quotas, versioned configuration |
| Identity | Argon2id passwords, RS256 JWTs with JWKS, refresh rotation with reuse detection, TOTP MFA and recovery codes, sessions and devices |
| End-user auth | OIDC-style authorization code + PKCE, hosted and embedded login, ID tokens, userinfo, RP-initiated logout, tenant membership verification, app-scoped sessions |
| Authorization | 80 permissions, 18 system roles, platform/organization/application/tenant scopes, custom roles, server-side policy checks |
| Billing | Plans, subscriptions, invoices, payments, MRR/ARR, metered usage and quota enforcement |
| Operations | Infrastructure inventory, deployments, incidents, monitoring alerts, health signals |
| Assurance | Append-only audit log, authentication events, request ids, security-relevant outcomes |
| Integration | OAuth 2.0 client credentials, scoped service tokens, `/v1/app/*` API, typed TypeScript SDK |
| Console | Next.js admin UI for every workflow above, with same-origin server-side API proxying |

## Acceptance scenarios

| Scenario | Where it is covered |
| --- | --- |
| A. Application onboarding | Console `/applications`; `POST /v1/applications`; environment and credential creation |
| B. Tenant onboarding | Console `/tenants`; `POST /v1/tenants`; owner invite, subscription, domain, quotas |
| C. Central login | `/login`; `POST /v1/auth/login`; MFA challenge, refresh rotation, sessions |
| D. Cross-tenant protection | PostgreSQL RLS plus `tenant_scope`; covered by `backend/tests/test_isolation.py` |
| E. Permissions | Policy engine and `require_platform`; 80 permissions / 18 roles; covered by auth and admin tests |
| F. Quota enforcement | `record_usage` checks limits in-transaction; `POST /v1/app/usage` returns `402 quota_exceeded` |
| G. Monitoring | `/v1/monitoring/alerts`, `/v1/infrastructure/*`, `/v1/incidents`; console `/operations` |
| H. Audit | Append-only `audit_logs` with revoked UPDATE/DELETE/TRUNCATE; console `/audit` |
| I. SDK integration | `packages/sdk-ts` plus `/v1/app/*`; demo credential seeded and printed by the API |
| J. Backup and recovery | `pg_dump`/`pg_restore` runbook in [`docs/OPERATIONS.md`](docs/OPERATIONS.md) |
| K. End-user sign-in | Hosted and embedded OAuth/OIDC flows; `backend/tests/test_auth_gateway.py`; `demo-app/` on `:3003` |

## Repository layout

```
backend/            FastAPI application, models, migrations, tests
  app/core/         config, db/RLS, deps, security, permissions, audit, rate limiting
  app/modules/      identity, access, organizations, applications, tenants, billing,
                    audit, dashboard, infrastructure, projects
  alembic/          schema, RLS policies, append-only audit, grants
web/                Next.js 16 admin console (server components + server actions)
demo-app/           Dependency-free external client for the auth gateway (port 3003)
packages/sdk-ts/    Typed TypeScript SDK for the integration API
infra/postgres/     Database bootstrap (non-owner runtime role, extensions)
docs/               Architecture, database, security, integration and operations
```

## Verification

```bash
# backend
cd backend
.\.venv\Scripts\python.exe -m pytest -q          # 77 tests
.\.venv\Scripts\ruff.exe check app tests
.\.venv\Scripts\ruff.exe format --check app tests
.\.venv\Scripts\alembic.exe check

# SDK
cd packages/sdk-ts && npm test && npm run build

# web
cd web && npm run typecheck && npm run build
```

The test suite runs against a disposable `basa_test` database as the real
non-owner runtime role, so isolation and RLS are exercised rather than mocked.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Database and tenant isolation](docs/DATABASE.md)
- [Security model](docs/SECURITY.md)
- [Integration guide](docs/INTEGRATION.md)
- [Operations and recovery](docs/OPERATIONS.md)
- [Roadmap and extension points](docs/ROADMAP.md)

## Configuration

All settings are validated at process start in `backend/app/core/config.py`. The
important ones:

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | Runtime connection as the non-owner `platform_app` role |
| `MIGRATION_DATABASE_URL` | Owner connection used only by Alembic |
| `REDIS_URL` | Shared rate limiting; in-process fallback is used when absent |
| `KEYS_DIR` | Location of the RS256 keypair for development |
| `JWT_PRIVATE_KEY` / `JWT_PUBLIC_KEY` | Inline PEM keys for managed environments |
| `ENCRYPTION_KEY` | urlsafe base64 32-byte key for encrypting stored secrets |
| `COOKIE_SECURE` | Must be `true` outside local development |
| `SMTP_HOST` | Transactional email transport; required in production for invitations and resets |
| `SEED_DEMO` | Must be `false` in production; production startup rejects demo seeding |
| `LOG_LEVEL` | Structured log level; production defaults to `INFO` |
| `SHOW_DEMO_CREDENTIALS` | Set `0` outside local development; the login page only advertises the seeded demo account when `1` |
| `CORS_ORIGINS` / `ALLOWED_ORIGINS` | Browser origins permitted to call the API (console `:3002`, demo `:3003`) |
| `PUBLIC_API_BASE_URL` | Public API origin the console uses to resume authorization requests |
| `APP_ACCESS_TOKEN_TTL_MINUTES` | Application end-user access-token lifetime (default 15) |
| `ID_TOKEN_TTL_MINUTES` | ID-token lifetime (default 15) |
| `APP_REFRESH_TOKEN_TTL_DAYS` | Application refresh-token lifetime (default 30) |
| `AUTH_CODE_TTL_SECONDS` | Authorization-code lifetime (default 60) |
| `SEED_ADMIN_EMAIL` / `SEED_ADMIN_PASSWORD` | Initial administrator |

## Production notes

- Run the API behind TLS and set `APP_ENV=production`, `COOKIE_SECURE=true`, a real
  `ENCRYPTION_KEY`, and managed key material (secret mount or KMS-backed injection).
- The runtime role must remain a non-owner with `NOBYPASSRLS`. Migrations are the only
  component that connects as the owner.
- Scale the API horizontally behind a load balancer. Redis is shared for rate limits;
  database sessions are per replica.
- Back up PostgreSQL and the Redis AOF volume. See the recovery runbook before relying
  on the platform in production.
- The auth gateway is first-party OIDC: customer applications consume it, but third
  parties cannot register clients and there is no consent screen. Social identity
  providers are not implemented.
- Hosted single sign-on across applications requires a shared cookie domain or a
  dedicated auth host. Browsers ignore ports in cookie scope, so port-based demos
  overstate SSO. Set `COOKIE_SECURE=true` and serve HTTPS end to end.
- The demo application and its seeded client secret are development-only. Never deploy
  `demo-app/` or the seeded secret to a shared environment.
