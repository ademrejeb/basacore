# Integration guide

Basacore exposes two integration paths:

| Path | Who uses it | Credential | Purpose |
| --- | --- | --- | --- |
| **End-user sign-in** | The application's frontend and backend | OIDC login client | Authenticate a person, bind them to a tenant, return identity, roles and permissions |
| **Control-plane API** | The application's backend | API credential (client credentials) | Resolve tenants, read config/flags, check authorization, meter usage, append audit |

Both are first-party: every client belongs to an application this company operates.
There is no third-party developer portal or consent screen.

---

## Part 1 — End-user sign-in

### 1.1 Create a login client

In the console open the application and use **Login clients → Create login client**.
The API equivalent:

```bash
curl -sS "$BASACORE_API/v1/applications/$APPLICATION_ID/clients" \
  -H "Authorization: Bearer $CONSOLE_TOKEN" \
  -H "content-type: application/json" \
  -d '{
    "name": "Atlas web",
    "client_type": "confidential",
    "redirect_uris": ["https://app.example.com/auth/callback"],
    "post_logout_redirect_uris": ["https://app.example.com/"],
    "scopes": ["openid", "profile", "email", "offline_access"],
    "pkce_required": true
  }'
```

The response contains `client_id` and, for confidential clients, a `client_secret`
shown exactly once. Public clients (SPAs, native apps) receive no secret and always
require S256 PKCE. Redirect URIs are matched exactly, including scheme, host, port and
path; there are no wildcards.

### 1.2 Hosted login (authorization code + PKCE)

This is the default and recommended flow: the user authenticates on Basacore and the
application only receives a short-lived code.

1. Generate `state`, `nonce`, and a PKCE verifier; derive
   `code_challenge = base64url(sha256(verifier))`.
2. Redirect the browser to the authorization endpoint, including the tenant hint:

```text
GET /v1/oauth/authorize
  ?response_type=code
  &client_id=basa_demo_atlas_web
  &redirect_uri=http%3A%2F%2Flocalhost%3A3003%2Fauth%2Fcallback
  &scope=openid%20profile%20email%20offline_access
  &state=<opaque-state>
  &nonce=<opaque-nonce>
  &code_challenge=<s256-challenge>
  &code_challenge_method=S256
  &tenant=northwind-sales
```

`tenant` accepts a tenant slug or UUID. It is a hint from the application, not an
authorization decision: Basacore checks that the tenant belongs to this application
and that the user holds an active membership before issuing anything. `prompt=login`
forces a fresh credential entry; `prompt=none` fails with `login_required` instead of
showing UI.

If the user is not signed in, Basacore redirects the browser to
`WEB_BASE_URL/login?return_to=…&app_name=…&tenant_name=…` and, after login, back to the
authorization endpoint. Register the application's callback URL exactly.

On success Basacore redirects to:

```text
https://app.example.com/auth/callback?code=<code>&state=<state>
```

Errors are returned the same way with `error` and `error_description` (for example
`access_denied` when membership does not match, `invalid_scope`, `login_required`).
Verify `state` before using the code.

### 1.3 Exchange the code

The exchange happens server-side for confidential clients:

```bash
curl -sS "$BASACORE_API/v1/oauth/token" \
  -H "content-type: application/x-www-form-urlencoded" \
  -u "$OIDC_CLIENT_ID:$OIDC_CLIENT_SECRET" \
  -d grant_type=authorization_code \
  -d code="$CODE" \
  -d redirect_uri="https://app.example.com/auth/callback" \
  -d code_verifier="$PKCE_VERIFIER"
```

Response:

```json
{
  "access_token": "eyJhbGciOiJSUzI1NiIs...",
  "token_type": "Bearer",
  "expires_in": 900,
  "refresh_token": "…",
  "refresh_expires_in": 2592000,
  "id_token": "eyJhbGciOiJSUzI1NiIs...",
  "scope": "openid profile email offline_access",
  "session_id": "…"
}
```

Authorization codes are single-use, expire after 60 seconds by default, and are bound
to the client, redirect URI, PKCE challenge and user. Replaying a consumed code is
treated as compromise: every session for that user/client is revoked.

### 1.4 Verify the ID token

Verify RS256 against the public keys before trusting any claim:

```bash
curl -sS "$BASACORE_API/.well-known/jwks.json"
```

Required checks: signature and `kid`, `iss` equals `JWT_ISSUER`, `aud` equals the
client id, `exp`/`iat` are valid, and `nonce` matches the authorization request.
Discovery documents are published at:

- `/.well-known/openid-configuration`
- `/.well-known/oauth-authorization-server`

Claims:

| Claim | Meaning |
| --- | --- |
| `sub` | Global Basacore user id (stable across applications) |
| `aud` | OAuth `client_id`; never the console audience |
| `iss` | Configured `JWT_ISSUER` |
| `app_id` | Application this login belongs to |
| `client_id` | Login client that requested it |
| `tenant_id`, `tenant_slug` | Verified tenant context |
| `email`, `email_verified`, `name`, `preferred_username` | Identity |
| `roles`, `permissions` | Effective grants recomputed when the token is issued |
| `sid` | Application session id; revocation is checked server-side |
| `auth_time`, `amr` | When and how the user authenticated (`pwd`, `mfa`) |
| `nonce` | Echo of the authorization request nonce (ID token only) |

### 1.5 Userinfo

```bash
curl -sS "$BASACORE_API/v1/oauth/userinfo" \
  -H "Authorization: Bearer $ACCESS_TOKEN"
```

Returns the same tenant-scoped identity, roles and permissions. The token's `sid` is
resolved against the server-side session on every call, so a revoked session fails
immediately even if the access token has not expired.

Application access tokens use `typ=user_app` and `aud=<client_id>`. They are rejected
by the console API, and console/service tokens are rejected by userinfo.

### 1.6 Refresh, revoke and logout

```bash
# Rotate the refresh token (the old one stops working)
curl -sS "$BASACORE_API/v1/oauth/token" \
  -u "$OIDC_CLIENT_ID:$OIDC_CLIENT_SECRET" \
  -d grant_type=refresh_token \
  -d refresh_token="$REFRESH_TOKEN"

# Revoke a refresh token (RFC 7009); access tokens are stateless
curl -sS "$BASACORE_API/v1/oauth/revoke" \
  -u "$OIDC_CLIENT_ID:$OIDC_CLIENT_SECRET" \
  -d token="$REFRESH_TOKEN"

# RP-initiated logout; also clears the Basacore console session cookies
GET /v1/oauth/logout?client_id=$OIDC_CLIENT_ID
  &post_logout_redirect_uri=https%3A%2F%2Fapp.example.com%2F
  &state=signed_out
```

Refresh tokens are opaque, hashed at rest, rotated on every use, and expire after
`APP_REFRESH_TOKEN_TTL_DAYS`. Reuse of a rotated token revokes the entire token family
for that user and client.

### 1.7 Embedded login

Use embedded login only when credentials must be collected inside your own UI. It is
available to confidential clients and must run on the server: the client secret never
goes to the browser.

```bash
curl -sS "$BASACORE_API/v1/oauth/embedded/login" \
  -H "content-type: application/json" \
  -d '{
    "client_id": "'"$OIDC_CLIENT_ID"'",
    "client_secret": "'"$OIDC_CLIENT_SECRET"'",
    "tenant": "northwind-sales",
    "email": "emma.wilson@northwind.dev",
    "password": "…",
    "scope": "openid profile email offline_access"
  }'
```

The response is either `{"status":"authenticated", ...tokens, user, tenant, application}`
or `{"status":"mfa_required","challenge_token":"…"}`. If policy requires MFA and the
user has not enrolled, embedded login returns `access_denied`; the user must complete
enrollment in the hosted console first. Complete MFA with:

```bash
curl -sS "$BASACORE_API/v1/oauth/embedded/mfa" \
  -H "content-type: application/json" \
  -d '{"client_id":"…","client_secret":"…","tenant":"northwind-sales",
       "challenge_token":"…","code":"123456"}'
```

Embedded login enforces the same password policy, lockout, tenant membership and MFA
rules as hosted login, and is rate limited per IP and client. It never creates a
console session; only an application-scoped session is issued.

### 1.8 Console MFA enrollment

Hosted login returns `{"status":"mfa_enrollment_required","challenge_token":"…"}` when
policy requires MFA for an account that has not enrolled. The console completes this
without a session:

```bash
# Start setup: returns secret + provisioning_uri
POST /v1/auth/mfa/setup/start   {"challenge_token":"…"}

# Confirm a TOTP code: returns the normal login payload + recovery codes
POST /v1/auth/mfa/setup/confirm {"challenge_token":"…","code":"123456"}
```

Re-enrolling an existing factor from the settings page requires the current password;
the active factor keeps working until the replacement is confirmed.

### 1.8 Demo application

`demo-app/` is a dependency-free Atlas CRM client running on
`http://localhost:3003` in the Compose stack. It demonstrates:

- hosted authorization-code + PKCE, with the verifier held server-side;
- RS256 verification of the ID token against the JWKS, including nonce and audience;
- userinfo, refresh rotation, revocation and RP-initiated logout;
- embedded login with a server-held confidential secret and an MFA step.

Seeded values:

```text
client_id      basa_demo_atlas_web
client_secret  bsc_demo_atlas_web_secret_2026
redirect_uri   http://localhost:3003/auth/callback
tenant         northwind-sales
demo user      emma.wilson@northwind.dev / Adm1n!Basa#2026
```

The container is configured with two API origins: `BASACORE_API_BASE_URL` is the public
origin used for browser redirects, and `BASACORE_INTERNAL_API_BASE_URL` (`http://api:8000`
in Compose) is used for server-side token, userinfo and JWKS calls. A real application
backend should make the same distinction.

### 1.9 Tenant rules

- Every application login is bound to exactly one tenant.
- A supplied hint must belong to the application and be `active` or `trial`.
- External users must have an active `TenantMembership` for that tenant.
- Internal staff may enter any tenant of the application but never another
  application's tenant.
- With no hint, exactly one active membership is accepted; zero returns
  `access_denied`, more than one returns `tenant_required`.

### 1.10 Error format

OAuth endpoints use the standard envelope:

```json
{ "error": "invalid_grant", "error_description": "The PKCE code_verifier is invalid." }
```

The authorization endpoint reports errors by redirecting to the registered
`redirect_uri` with `error`, `error_description` and the original `state`. An
unregistered `redirect_uri` is rejected directly and never used as a redirect target.

---

## Part 2 — Control-plane API (server-to-server)

The integration API lets an application backend use Basacore as its control plane:
resolve tenants, read effective configuration and flags, check permissions, report
usage and append audit events.

### 2.1 Issue a credential

1. Register the application in the console (`/applications`).
2. Open the application and create an API credential.
3. Select only the scopes the integration needs.
4. Copy the secret immediately. Only the prefix is stored in clear.

The credential id is the OAuth `client_id`; the secret is the `client_secret`.

### 2.2 Obtain a token

`POST /v1/oauth/token` with `application/x-www-form-urlencoded`:

```bash
curl -sS http://localhost:8000/v1/oauth/token \
  -H "content-type: application/x-www-form-urlencoded" \
  -d grant_type=client_credentials \
  -d client_id="$BASACORE_CLIENT_ID" \
  -d client_secret="$BASACORE_CLIENT_SECRET"
```

Response:

```json
{
  "access_token": "eyJhbGciOiJSUzI1NiIs...",
  "token_type": "Bearer",
  "expires_in": 3600,
  "scope": "app:config.read app:usage.write"
}
```

Tokens are RS256 JWTs bound to one application. Send them as
`Authorization: Bearer <token>` to `/v1/app/*`. Presenting a token to a different
application's resources returns `404`, not `403`, to avoid resource enumeration.

### 2.3 Scopes

| Scope | Grants |
| --- | --- |
| `app:config.read` | Effective tenant configuration, feature flags and plan context |
| `app:tenant.resolve` | Tenant list, lookup by id/slug/domain |
| `app:identity.read` | Tenant memberships and identity summaries |
| `app:authorization.check` | Permission decision for a user inside a tenant |
| `app:usage.write` | Metered usage writes, quota-enforced |
| `app:audit.write` | Append application audit events |

Requests missing a required scope return `403` with `error.code = "forbidden"`.

### 2.4 Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/v1/app/me` | Application and credential identity |
| `GET` | `/v1/app/tenants` | List tenants (limit/offset/status) |
| `GET` | `/v1/app/tenants/resolve` | Resolve by `id`, `slug` or `domain` |
| `GET` | `/v1/app/config` | Effective config, features, plan, usage |
| `POST` | `/v1/app/authorization/check` | Check a user permission in a tenant |
| `POST` | `/v1/app/usage` | Report metered usage |
| `GET` | `/v1/app/usage/summary` | Current-period usage for a tenant |
| `POST` | `/v1/app/audit` | Append an audit event |

#### Resolve a tenant

```bash
curl -sS "$BASACORE_API/v1/app/tenants/resolve?slug=lumen-health" \
  -H "Authorization: Bearer $TOKEN"
```

#### Effective configuration

```bash
curl -sS "$BASACORE_API/v1/app/config?tenant_id=$TENANT_ID" \
  -H "Authorization: Bearer $TOKEN"
```

The response merges application-wide configuration with environment-specific values,
resolves per-tenant feature overrides, includes the plan limits and current usage, and
returns the configuration version that produced the result.

#### Authorization check

```bash
curl -sS "$BASACORE_API/v1/app/authorization/check" \
  -H "Authorization: Bearer $TOKEN" \
  -H "content-type: application/json" \
  -d '{"user_id":"<uuid>","tenant_id":"<uuid>","permission":"projects.read"}'
```

```json
{
  "authorized": true,
  "user_id": "...",
  "tenant_id": "...",
  "permission": "projects.read",
  "effective_permissions": ["projects.read", "projects.write"]
}
```

The check is evaluated from stored role grants, not from client-supplied roles.

#### Usage

```bash
curl -sS "$BASACORE_API/v1/app/usage" \
  -H "Authorization: Bearer $TOKEN" \
  -H "content-type: application/json" \
  -d '{
    "tenant_id":"<uuid>",
    "metric":"api_calls",
    "value":25,
    "idempotency_key":"req-2026-11-05-0001"
  }'
```

- `metric` must match `^[a-z0-9._-]+$`.
- `value` must be positive.
- The quota check and the insert happen in one transaction. A write that would exceed
  the limit returns `402` with `error.code = "quota_exceeded"` and the metric, limit and
  used values.
- `idempotency_key` is unique per application. Repeating a key returns the original
  record with `"idempotent": true` instead of double-counting.

#### Audit

```bash
curl -sS "$BASACORE_API/v1/app/audit" \
  -H "Authorization: Bearer $TOKEN" \
  -H "content-type: application/json" \
  -d '{
    "action":"project.created",
    "resource_type":"project",
    "resource_id":"prj_123",
    "tenant_id":"<uuid>",
    "outcome":"success",
    "metadata":{"source":"atlas-api"}
  }'
```

Audit rows are append-only. The application scope is recorded from the credential, never
from the request body.

### 2.5 TypeScript SDK

The client lives in `packages/sdk-ts` and is built with TypeScript 7.

```bash
cd packages/sdk-ts
npm ci
npm run build
npm test
```

```ts
import { BasacoreClient, BasacoreApiError } from "@basacore/sdk";

const basacore = new BasacoreClient({
  baseUrl: process.env.BASACORE_API_URL!,
  clientId: process.env.BASACORE_CLIENT_ID!,
  clientSecret: process.env.BASACORE_CLIENT_SECRET!,
  timeoutMs: 10_000,
});

try {
  const tenant = await basacore.resolveTenant({ slug: "lumen-health" });
  const context = await basacore.getTenantConfig(tenant.id);

  if (!context.features["new_checkout"]) {
    // Keep the old checkout path.
  }

  const decision = await basacore.checkAuthorization({
    userId: currentUser.id,
    tenantId: tenant.id,
    permission: "projects.read",
  });
  if (!decision.authorized) throw new Error("Forbidden");

  await basacore.reportUsage({
    tenantId: tenant.id,
    metric: "api_calls",
    value: 1,
    idempotencyKey: requestId,
  });
} catch (error) {
  if (error instanceof BasacoreApiError) {
    console.error(error.status, error.code, error.requestId);
  }
  throw error;
}
```

The client caches the token in memory, refreshes 30 seconds before expiry, retries once
on `401`, and never logs the secret.

### 2.6 Control-plane error envelope

Every control-plane error uses the same shape:

```json
{
  "error": {
    "code": "quota_exceeded",
    "message": "Quota exceeded for api_calls.",
    "details": { "metric": "api_calls", "limit": 1000, "used": 1000 },
    "request_id": "req_..."
  }
}
```

| Status | Typical codes |
| --- | --- |
| `400` | `bad_request`, `validation_failed` |
| `401` | `unauthorized`, `invalid_token` |
| `403` | `forbidden`, `insufficient_scope`, `invalid_origin` |
| `404` | `not_found` (including cross-application tenant ids) |
| `409` | `conflict` |
| `402` | `quota_exceeded` |
| `422` | `validation_failed` |
| `429` | `rate_limited` |
| `5xx` | `internal_error` |

Include `request_id` in support requests; it correlates API logs, audit rows and the
response header.

### 2.7 Operational guidance

- Keep credentials per environment; never reuse production secrets in staging.
- Rotate by creating a new credential, deploying it, then revoking the old one.
- Cache tenant configuration briefly (seconds, not minutes) and refresh on a version
  change or webhook in a future increment.
- Send usage after the work succeeds, with a deterministic idempotency key per request.
- Treat a `402` as a business outcome, not a transient failure: stop metered work or ask
  the tenant to upgrade.
- Keep login clients and API credentials separate: they have different audiences and
  cannot be substituted for each other.
- Run the SDK's tests in CI; they cover token caching, retry, timeout and error mapping.
