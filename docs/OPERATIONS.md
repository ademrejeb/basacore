# Operations and recovery

This runbook covers the containerized stack, health checks, backups, restore drills,
upgrades, rollback and secret rotation. It is written against the default Compose
project (`basacore`) and database (`basa`). Replace names if you deploy differently.

## 1. Runtime topology

| Service | Image | Host port | Persistence |
| --- | --- | --- | --- |
| `db` | `postgres:17-alpine` | `5434` | Named volume `db-data` |
| `redis` | `redis:7-alpine` | `6380` | Named volume `redis-data`, AOF enabled |
| `api` | `basacore-api` | `8000` | Named volume `keys-data` at `/app/keys` |
| `web` | `basacore-web` | `3002` | None (stateless) |
| `demo-app` | `basacore-demo-app` | `3003` | None (stateless, development only) |

PostgreSQL is the only system of record. Redis holds rate-limit counters; losing it
resets those counters but does not lose identities, sessions or audit history. The web
console and demo app are stateless. The `keys-data` volume holds development signing and
encryption keys; in a managed deployment, inject key material instead and back up the
secret manager.

The `demo-app` service is an external client used to demonstrate the auth gateway. It
is not part of Basacore, holds only in-memory sessions, and must not be deployed to a
shared or production environment.

### Startup order

`docker compose up` starts PostgreSQL and Redis, waits for both health checks, then starts
the API. The API entrypoint:

1. runs `alembic upgrade head` as `platform_owner` when `RUN_MIGRATIONS=true`;
2. runs `python -m app.seed` when `SEED_DEMO=true`, otherwise
   `python -m app.seed --reference` when `SEED_REFERENCE=true` (entrypoint default);
3. starts Uvicorn as the non-root `basacore` user with the `platform_app` runtime role.

The web container starts only after the API is healthy. The development Compose file
also starts `demo-app`, which waits for the same health check; it has no database or
volume of its own.

## 2. Health and readiness

```bash
docker compose ps
curl -fsS http://localhost:8000/healthz     # liveness: {"status":"ok"}
curl -fsS http://localhost:8000/readyz      # database + redis dependency state
curl -fsS http://localhost:8000/.well-known/jwks.json
curl -fsS http://localhost:8000/.well-known/openid-configuration
curl -fsS http://localhost:3002/login > /dev/null
curl -fsS http://localhost:3003/healthz   # demo client, development only
```

`/readyz` returns `503` only when PostgreSQL is unreachable. Redis is reported as
`ok`, `degraded` or `not_configured`; a degraded Redis keeps the API ready because the
rate limiter falls back to bounded in-process state. Treat `degraded` as a warning, not
an outage.

Useful commands:

```bash
docker compose logs -f api
docker compose logs -f web
docker compose exec db pg_isready -U platform_owner -d basa
docker compose exec redis redis-cli ping
docker compose exec redis redis-cli info persistence
```

Structured logs are JSON when `APP_ENV=production`; each request carries `X-Request-ID`,
which also appears in error responses and audit rows.

## 3. Configuration for non-local use

Minimum production changes:

| Variable | Required value |
| --- | --- |
| `APP_ENV` | `production` |
| `COOKIE_SECURE` | `true` |
| `ENCRYPTION_KEY` | 32-byte urlsafe base64 key from a secret manager |
| `JWT_PRIVATE_KEY` / `JWT_PUBLIC_KEY` | PEM material, or a backed-up `keys-data` volume |
| `CORS_ORIGINS` / `ALLOWED_ORIGINS` | Exact console origins over HTTPS |
| `SEED_DEMO` | `false` |
| `SMTP_HOST` / `SMTP_FROM` | A working transactional email transport |
| `LOG_LEVEL` | `INFO` for access logs (default) |
| `SHOW_DEMO_CREDENTIALS` | `0` |
| `POSTGRES_*_PASSWORD` | Strong, separately managed values |
| `JWT_ISSUER` / `API_BASE_URL` / `WEB_BASE_URL` | Public HTTPS URLs |

`get_settings()` refuses to start in `production`/`staging` when `ENCRYPTION_KEY` or JWT
private key material is missing, or when `COOKIE_SECURE` is false. Keep
`MIGRATION_DATABASE_URL` available only to the release job or entrypoint; do not expose
the owner password to the web tier.

## 4. Backup

### What to back up

| Asset | Frequency | Why |
| --- | --- | --- |
| PostgreSQL `basa` (custom-format dump) | Daily, plus before every migration | All platform state, including audit |
| Off-host copy of the dump + SHA-256 | Every backup | Ransomware/disk-loss protection |
| Key material (`keys-data` volume or injected PEM/`ENCRYPTION_KEY`) | On creation and after rotation | JWT verification and encrypted secrets |
| Redis AOF (`redis-data`) | Optional | Rate-limit counters only |
| `.env` / secret-manager export | On change | Reproducible recovery |

A logical dump is consistent without stopping the API. Keep at least one dump outside the
host that runs Docker. Encrypt backups at rest (`age`, GPG or the storage provider's
encryption) because the dump contains password hashes, audit history and encrypted
integration secrets.

Suggested retention: 30 daily, 12 weekly, 12 monthly. Test a restore at least monthly.

### Database backup (PowerShell)

```powershell
$ErrorActionPreference = "Stop"
$dir = "D:\backups\basacore"
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$name = "basa-$stamp.dump"

# Dump inside the container, then copy the binary file to the host.
docker compose exec -T db pg_dump -U platform_owner -d basa -Fc -f "/tmp/$name"
docker compose cp "db:/tmp/$name" "$dir\$name"
docker compose exec -T db rm -f "/tmp/$name"

Get-FileHash "$dir\$name" -Algorithm SHA256 |
  ForEach-Object { "$($_.Hash)  $name" } |
  Out-File -Encoding ascii "$dir\$name.sha256"
```

Do not use `>` to capture `pg_dump` output in PowerShell; redirection can corrupt the
binary format. Dump to a container path and copy it.

### Database backup (POSIX shell)

```bash
set -eu
dir=/var/backups/basacore
mkdir -p "$dir"
name="basa-$(date +%Y%m%d-%H%M%S).dump"
docker compose exec -T db pg_dump -U platform_owner -d basa -Fc -f "/tmp/$name"
docker compose cp "db:/tmp/$name" "$dir/$name"
docker compose exec -T db rm -f "/tmp/$name"
sha256sum "$dir/$name" > "$dir/$name.sha256"
```

### Key-material backup

Development and single-host deployments keep keys in the named volume:

```bash
docker run --rm -v basacore_keys-data:/keys -v "$PWD/backups:/out" alpine \
  tar -czf /out/keys-$(date +%Y%m%d).tar.gz -C /keys .
```

Store that archive with the same care as the database dump. Losing the JWT key pair
invalidates outstanding access tokens (users sign in again; refresh tokens remain valid
because they are checked against the database). Losing `ENCRYPTION_KEY` or
`keys/encryption.key` makes encrypted integration secrets permanently unreadable. There
is no automatic re-encryption job; restore the original key.

If keys are injected with `JWT_PRIVATE_KEY`, `JWT_PUBLIC_KEY` and `ENCRYPTION_KEY`, the
`keys-data` volume is not authoritative. Back up the secret-manager entries instead and
verify they can be read during a recovery exercise.

### Redis

Redis is not authoritative. If rate-limit continuity matters, copy the AOF volume with
the API stopped:

```bash
docker compose stop api
docker run --rm -v basacore_redis-data:/data -v "$PWD/backups:/out" alpine \
  tar -czf /out/redis-$(date +%Y%m%d).tar.gz -C /data .
docker compose start api
```

## 5. Restore drill (verified)

This drill restores the latest dump into a scratch database while the stack keeps
running. It was executed against the Compose stack on 2026-11-05; the restored database
contained 43 public tables, the current Alembic revision, all users/tenants/audit rows,
15 RLS policies, and RLS still blocked unscoped reads by `platform_app`.

```powershell
$ErrorActionPreference = "Stop"
$dir = "D:\backups\basacore"
$dump = Get-ChildItem "$dir\basa-*.dump" | Sort-Object LastWriteTime | Select-Object -Last 1
$scratch = "basa_restore_drill"

# 1. Recreate the scratch database and required extensions.
docker compose exec -T db psql -U platform_owner -d postgres -v ON_ERROR_STOP=1 `
  -c "DROP DATABASE IF EXISTS $scratch WITH (FORCE);"
docker compose exec -T db psql -U platform_owner -d postgres -v ON_ERROR_STOP=1 `
  -c "CREATE DATABASE $scratch OWNER platform_owner;"
docker compose exec -T db psql -U platform_owner -d $scratch -v ON_ERROR_STOP=1 `
  -c "CREATE EXTENSION IF NOT EXISTS pgcrypto; CREATE EXTENSION IF NOT EXISTS citext;"

# 2. Restore with ACLs and ownership intact (do not pass --no-owner/--no-privileges).
docker compose cp $dump.FullName "db:/tmp/restore-drill.dump"
docker compose exec -T db pg_restore -U platform_owner -d $scratch `
  --exit-on-error /tmp/restore-drill.dump
docker compose exec -T db rm -f /tmp/restore-drill.dump

# 3. Structural verification.
docker compose exec -T db psql -U platform_owner -d $scratch -At -c @"
select 'tables=' || count(*) from information_schema.tables where table_schema='public';
select 'alembic=' || version_num from alembic_version;
select 'users=' || count(*) from users;
select 'tenants=' || count(*) from tenants;
select 'audit=' || count(*) from audit_logs;
select 'policies=' || count(*) from pg_policies where schemaname='public';
select 'tenants_rls=' || relrowsecurity from pg_class where relname='tenants';
"@
```

Then prove isolation and audit immutability as the runtime role:

```powershell
# No tenant context: RLS must hide every tenant.
docker compose exec -T -e PGPASSWORD=change-me-app db `
  psql -U platform_app -d basa_restore_drill -At -c "select count(*) from tenants;"
# Expected: 0

# With a tenant context: exactly that tenant is visible.
$tenant = (docker compose exec -T db psql -U platform_owner -d basa_restore_drill -At `
  -c "select id from tenants order by created_at limit 1;").Trim()
docker compose exec -T -e PGPASSWORD=change-me-app db psql -U platform_app -d basa_restore_drill -At `
  -c "select set_config('app.tenant_id','$tenant',false); select count(*) from tenants;"
# Expected: 1

# Append-only audit: this must fail with "permission denied for table audit_logs".
docker compose exec -T -e PGPASSWORD=change-me-app db `
  psql -U platform_app -d basa_restore_drill -c "update audit_logs set action='tampered';"
# Expected: ERROR: permission denied for table audit_logs
```

Drop the scratch database when the drill is recorded:

```bash
docker compose exec -T db psql -U platform_owner -d postgres \
  -c "DROP DATABASE IF EXISTS basa_restore_drill WITH (FORCE);"
```

### Common restore failures

| Symptom | Cause | Fix |
| --- | --- | --- |
| `type "citext" does not exist` | Extensions were not created in the scratch database | Run the `CREATE EXTENSION` step before `pg_restore` |
| `role "platform_app" does not exist` | Restoring on a different cluster without the runtime role | Run `infra/postgres/init/01-roles.sh` or create the role first |
| `permission denied for table ...` after restore | Restored without ACLs (`--no-privileges`) | Restore again with ACLs, or re-run the grants migration |
| `database is being accessed by other users` | Live connections to the target | Use `WITH (FORCE)` for a scratch DB; stop API/web for a production cutover |
| `relation already exists` | Restoring into a non-empty database | Recreate the target database first |

## 6. Production restore / disaster recovery

1. **Declare the incident and freeze writes.** Stop `web` and `api`:
   `docker compose stop web api`. Keep `db` and `redis` running.
2. **Preserve the damaged database.** Create a copy before changing it:
   `ALTER DATABASE basa RENAME TO basa_damaged_<timestamp>;`
3. **Create a clean database** owned by `platform_owner`, with `pgcrypto` and `citext`.
4. **Restore the chosen dump** with ownership and ACLs.
5. **Reconcile the schema.** Run `alembic upgrade head` with `MIGRATION_DATABASE_URL` if
   the dump predates the current release. The API entrypoint does this automatically.
6. **Sync reference data** (permissions, roles, policy):
   `python -m app.seed --reference`. Do not run the demo seed in production.
7. **Restore key material.** Mount the matching keys volume or inject the original
   `JWT_PRIVATE_KEY`/`JWT_PUBLIC_KEY` and `ENCRYPTION_KEY`. Mismatched encryption keys
   make stored integration secrets unreadable.
8. **Start and verify.**
   `docker compose up -d api web`, then check `/readyz`, sign in as an administrator,
   open a tenant, and confirm the audit trail shows the pre-incident rows.
9. **Confirm isolation.** Re-run the RLS checks from the drill against the restored
   database before removing the freeze.
10. **Record the recovery** in the incident log: dump used, checksum, start/end time,
    data-loss window, and follow-up actions.

Recovery point objective depends on dump frequency; with daily dumps it is up to 24
hours. For a lower RPO, configure PostgreSQL WAL archiving and point-in-time recovery at
the infrastructure layer; the Compose stack does not configure it.

## 7. Upgrade and rollback

### Upgrade

1. Take and verify a dump.
2. Pull/build the new images: `docker compose build`.
3. Run `docker compose up -d`. The API entrypoint applies migrations before serving.
4. Check `/readyz`, then run the smoke check in section 8.
5. Keep the previous images tagged until the new release is accepted.

### Rollback

Database migrations are reversible, but restoring the pre-upgrade dump is the safe
rollback for production. A schema downgrade can discard columns or fail on data written
by the newer release.

- **Application-only regression:** redeploy the previous image tag; if no migration ran,
  no database action is needed.
- **Migration regression:** stop `api`/`web`, restore the pre-upgrade dump into a clean
  database, deploy the previous image, and start. Do not casually run
  `alembic downgrade` on a database that has accepted writes from the newer schema.
- **Audit history:** never truncate or rewrite `audit_logs` during a rollback. Restore
  the backup instead; the append-only triggers will reject edits.

## 8. Smoke check after deployment

```bash
curl -fsS http://localhost:8000/healthz
curl -fsS http://localhost:8000/readyz
curl -fsS http://localhost:3002/login > /dev/null
curl -fsS http://localhost:3003/healthz > /dev/null   # development stack only
```

Then, through the console proxy, verify an authenticated session and a few pages:

```powershell
$c = New-Object Microsoft.PowerShell.Commands.WebRequestSession
Invoke-RestMethod -Method Post -Uri http://localhost:3002/api/proxy/v1/auth/login `
  -WebSession $c -ContentType "application/json" `
  -Body '{"email":"admin@basa.dev","password":"Adm1n!Basa#2026"}'
"/dashboard","/reports","/settings","/sdk","/tenants","/audit" | ForEach-Object {
  $r = Invoke-WebRequest -Uri "http://localhost:3002$_" -WebSession $c
  "$_ $($r.StatusCode)"
}
```

For the integration path, exchange the seeded or rotated client credentials at
`/v1/oauth/token` and call `/v1/app/me`, `/v1/app/tenants` and `/v1/app/usage/summary`.

For the end-user gateway, open the demo app at `http://localhost:3003`, complete hosted
login and confirm that `/v1/oauth/userinfo` shows the expected tenant, roles and
permissions. Then run the embedded login and **Sign out everywhere** to confirm
revocation.

## 9. Secret and credential rotation

| Secret | Procedure | Effect |
| --- | --- | --- |
| User password | Settings → Change password, or password-reset flow | Other sessions stay valid unless revoked |
| API credential | Create a new credential with the same scopes, deploy it, then revoke the old one | Old tokens fail immediately; no downtime |
| Login client secret | Console → application → Login clients → Rotate secret, update the app secret in the same window | Previous secret fails immediately; existing app sessions remain until revoked |
| Login client disable | Console → application → Login clients → Disable | No new codes or tokens; every outstanding session for that client is revoked |
| MFA device | Disable MFA with the password, then enroll again | Recovery codes are replaced |
| Database passwords | `ALTER ROLE ... PASSWORD ...`, update the secret, restart `api` | Brief API restart; existing pooled connections close |
| JWT signing key | Replace the PEM in `keys-data` or the injected variables, restart `api` | All access tokens issued by the old key become invalid; users sign in again |
| `ENCRYPTION_KEY` | Not hot-rotatable in this release | Requires decrypting and re-encrypting affected secrets; schedule maintenance and keep the old key until complete |
| Seed/demo credentials | Set `SEED_DEMO=false`; rotate the demo admin password | Removes the known development credential from new deployments |

Rotate API credentials per environment. Never reuse a production secret in staging.

## 10. Routine checks

Daily:

- `docker compose ps` — all five services healthy (the demo app only exists in the development stack).
- `/readyz` — database `ok`; investigate Redis `degraded`.
- Backup completed, checksum written, copy present off-host.
- Disk space on the Docker host and backup target.

Weekly:

- Review `/audit` for denied and failed outcomes.
- Review `/operations` for unresolved incidents and active alerts.
- Review `/reports` for tenants over quota or unexpectedly idle.
- Check certificate expiry and clock synchronization.

Monthly:

- Run the restore drill in section 5 and record the result.
- Verify key-material backups open and match the active `kid` from JWKS.
- Review administrator accounts, roles and API credentials; revoke unused ones.
- Apply base-image and dependency updates, then run the full verification suite.

## 11. Troubleshooting

| Symptom | Likely cause | Resolution |
| --- | --- | --- |
| API exits at boot with `Missing required production configuration` | `APP_ENV=production` without keys or with `COOKIE_SECURE=false` | Supply the missing secret or fix the cookie setting |
| `/readyz` returns `503` | PostgreSQL unreachable or credentials changed | Check `docker compose logs db`, role password, `DATABASE_URL` |
| `/readyz` shows Redis `degraded` | Redis down or unreachable | Restore Redis; rate limits fall back per process until then |
| Console login succeeds but pages redirect to `/login` | Cookies rejected (HTTPS/`COOKIE_SECURE` mismatch or wrong host) | Align `WEB_BASE_URL`, TLS termination and `COOKIE_SECURE`; confirm `basa_access` is set |
| `403 invalid_origin` | `Origin` not in `ALLOWED_ORIGINS` | Add the exact console origin and restart the API |
| Tenant pages show no rows | Missing tenant context, or RLS policy regression | Verify `tenant_scope` usage and run `test_isolation.py` |
| `permission denied for table` after restore | ACLs or default privileges missing | Restore with ACLs; re-run the grants migration |
| `alembic check` reports new operations | Model changed without a migration | Generate and review a migration; never auto-generate against production |
| Migration waits on a lock | Long-running transaction or idle session | Inspect `pg_stat_activity`, terminate the blocker, retry |
| Integration gets `402 quota_exceeded` | Tenant hit its plan/tenant limit | Raise the quota or wait for the next period; this is expected behavior |
| Integration gets `404` for a tenant id | Credential belongs to another application | Use the tenant id resolved for this credential; cross-application access is deliberately hidden |
| Integration gets `401` | Token expired or credential revoked | Request a new token; rotate the credential if revoked |

For any incident, collect the `X-Request-ID` from the response and correlate it with
`docker compose logs api` and the audit row.
