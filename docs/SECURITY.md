# Security model

## Principles

- Every protected operation authorizes on the server. The console hides controls for
  convenience, but hiding is never the enforcement.
- Tenant identity is derived from the authenticated principal or the requested resource
  and then verified against the tenant boundary. It is never read from a client header.
- Defence is layered: authorization → application scope checks → PostgreSQL RLS → audit.
- Secrets are hashed or encrypted at rest; plaintext is returned only at creation time.
- Audit is append-only at the privilege, trigger and API layers.

## Authentication

| Control | Implementation |
| --- | --- |
| Password hashing | Argon2id with per-password salt and rehash-on-login |
| Password policy | Minimum 12 characters, strength validation, rejection of the current password |
| Access tokens | RS256 JWT, 15-minute default, `iss`/`aud`/`sub`/`sid`/`jti`/`exp` |
| Key distribution | JWKS at `/.well-known/jwks.json`, stable `kid` derived from the public key |
| Refresh tokens | Opaque 32-byte tokens, stored hashed, rotated on every refresh |
| Reuse detection | Presenting a rotated refresh token revokes the session family |
| Sessions | Device rows with IP, user agent, creation, last seen and expiry; revocable individually or all at once |
| MFA | TOTP (6 digits, 30-second window) plus single-use recovery codes stored hashed |
| MFA challenge | Short-lived signed challenge token, separate from the access token; `enroll` challenges bootstrap setup when policy requires MFA |
| Invitations | Single-use hashed verification tokens with expiry; a resend replaces the stored token instead of failing, and tokens are omitted from API responses in production |
| Password reset | Single-use hashed tokens, uniform response to avoid account enumeration, enforced history policy |
| Password history | The last `password_history` hashes are stored and rejected on change/reset/invite |
| MFA enrollment | A staged secret is only promoted after a valid code; re-enrollment requires the current password |
| Session cap | Security policy enforces a maximum number of concurrent sessions per user |
| Idle timeout | `session_idle_minutes` is enforced on refresh; an idle session is revoked and audited as `session_idle_timeout` |
| Lockout | Failed sign-in counter and lock window, configurable through the security policy |
| Application access tokens | RS256 JWT, `typ=user_app`, `aud=<client_id>`, 15-minute default, bound to an application session (`sid`) |
| ID tokens | RS256 JWT, `typ=id`, `aud=<client_id>`, `nonce` echo, identity/tenant/role claims |
| Authorization codes | Opaque, stored hashed, single-use, 60-second default, bound to client, redirect URI, PKCE challenge and user |
| Login client secrets | Prefixed (`bsc_…`), SHA-256 hashed at rest, shown once; rotation invalidates the previous secret immediately |
| Refresh tokens (application) | Opaque, stored hashed, rotated on every use, reuse revokes the token family |

Access tokens are accepted from an httpOnly cookie (`basa_access`) or an
`Authorization: Bearer` header. Refresh cookies are httpOnly and same-site, with the
path set to `/` so the console's same-origin proxy can rotate them.

## End-user auth gateway

External applications delegate sign-in to Basacore. The gateway is OIDC-shaped
(authorization code + PKCE, `id_token`, `userinfo`, RP-initiated logout) but is
first-party only: there is no developer self-service and no consent screen.

### Token separation

Three token families exist and none is interchangeable:

| Token | `typ` | `aud` | Accepted by |
| --- | --- | --- | --- |
| Console session | `user` | `basacore` | Console and admin API only |
| Application end-user | `user_app` | `<client_id>` | `/v1/oauth/userinfo`; the owning application's backend |
| Service (client credentials) | `service` | application | `/v1/app/*` only |

`decode_app_token` pins `typ=user_app` and the audience; the console principal path
rejects anything else. Sending an app token to `/v1/auth/me` or a console token to
`/v1/oauth/userinfo` returns `401`, which is asserted by the gateway tests.

### Authorization request

- `response_type=code` only. Unsupported values return `unsupported_response_type`.
- `client_id` must be an active login client; unknown clients return `invalid_client`.
- `redirect_uri` must match a registered URI byte for byte. There are no wildcards and
  no prefix matching; a mismatch is rejected directly and the untrusted URI is never
  used as a redirect target.
- S256 PKCE is mandatory for public clients and enforced whenever the client requires
  it. The verifier is checked with a constant-time comparison; a mismatch does not burn
  the code, so a transient client bug is recoverable.
- `tenant` is a hint, never an authorization decision. It must belong to the client's
  application, be `active` or `trial`, and the user must hold an active
  `TenantMembership`. A mismatch returns `access_denied` with the original `state`.
- `state` and `nonce` are echoed unchanged. Clients must verify both.
- `prompt=login` forces a fresh credential entry; `prompt=none` returns
  `login_required` instead of rendering UI. `prompt` is stripped when the login page
  replays the request, so the flow cannot loop.

### Code and refresh handling

- Codes are consumed inside a transaction with a row lock; a second exchange fails with
  `invalid_grant`.
- Replaying a consumed code is treated as compromise: the application sessions created
  from it are revoked.
- The token request re-checks the client, secret, exact redirect URI, PKCE verifier,
  code expiry and the user's current status. Tenant status and membership are re-read
  before issuing.
- Refresh tokens rotate on every use. Presenting an already-rotated token revokes the
  entire family for that user and client, and consequently the access tokens bound to
  the revoked session fail at `userinfo`.
- A refresh request can only narrow or repeat the originally granted scopes; widening
  returns `invalid_scope`.
- Disabling a login client is a kill switch: no new codes or tokens are issued and
  every outstanding session is revoked.

### Embedded login

`POST /v1/oauth/embedded/login` and `/v1/oauth/embedded/mfa` exist only for
confidential clients and must be called from an application backend. The client secret
and the resulting tokens never reach browser JavaScript. Embedded login applies the
same password policy, lockout, MFA and tenant membership checks as hosted login, and
never creates a console session. If MFA is required it returns an opaque
`challenge_token`; the password is not carried forward.

### Gateway rate limits

| Endpoint | Limit |
| --- | --- |
| Token endpoint per IP + client | 60 / minute |
| Embedded login per IP + client | 20 / 5 minutes |
| Embedded MFA per IP + client | 20 / 5 minutes |

Hosted sign-in inherits the identity rate limits above because it uses the same login
endpoint.

### Cookies and SSO

The console session is a cookie on the web origin. Hosted login therefore gives
single sign-on across applications only when the browser already holds that cookie.

Browsers ignore ports in cookie scope: `localhost:3002` and `localhost:3003` share
`localhost` cookies. The demo consequently shows SSO locally, which is **not** a
production guarantee. Production SSO needs either a shared parent `COOKIE_DOMAIN`
(e.g. `auth.example.com` plus `*.example.com`) or a dedicated auth host, and
`COOKIE_SECURE=true` with HTTPS throughout. Applications on unrelated registrable
domains do not share a session by design; each performs its own authorization-code
round trip.

### Gateway secrets

- Client secrets are SHA-256 hashed; only the display prefix is retained and the full
  value is returned once at creation or rotation.
- Authorization codes and application refresh tokens are stored hashed, never in clear.
- The gateway never logs passwords, codes, tokens or client secrets.

The console's admin client management is itself permission-gated and audited like any
other administrative mutation.

## Authorization

The catalogue in `app/core/permissions.py` defines 80 permissions and 18 system roles
across four scope types. Roles are grants, not identities:

```
role_permissions ── role ── user_roles(scope_type, scope_id)
```

`require_platform(permission)` checks an authenticated principal and returns `403`
before the handler runs. Tenant and application routes resolve membership and role
grants inside the relevant scope. Custom roles can only be granted by an actor with
`roles.manage`.

Delegation is capped, not transitive: an actor may only create or update a role, or
assign a role to a user, when every permission in that role is already held by the
actor in the same scope (`permissions.exceeded_grants`). Without the ceiling,
`roles.manage` alone was a ladder to `settings.manage`, `secrets.manage` or the
`super_admin` wildcard.

Platform reads that legitimately span tenants enter `platform_scope` only after the
permission check. That sets `app.bypass_rls=on` for the transaction and clears it
durably before the connection returns to the pool.

## Security policy

`GET/PUT /v1/access/security-policy` and `GET /v1/access/security-policy/scopes`
read and write `SecurityPolicy` rows. Policies are stored per scope (platform,
organization, application, tenant) and merged broadest to narrowest at use time, so
sign-in, refresh and password changes apply one effective policy.

Writes are validated against a closed key set with per-key bounds. Unknown keys,
out-of-range integers and malformed CIDRs are rejected with `422` rather than being
stored and ignored. Sign-in resolves the policy rows that apply to the user's actual
organization, application and tenant grants, so narrower scopes are not inert.

## CSRF and browser boundaries

- Authentication cookies are httpOnly, `SameSite=Lax`, and `Secure` in production.
- Cookie-authenticated unsafe requests must include an `Origin` matching
  `ALLOWED_ORIGINS`. Bearer-authenticated machine clients are unaffected.
- The web console calls the API server-side. The browser only talks to the console
  origin, so cross-origin cookie exposure is avoided.
- `/api/proxy/*` forwards only the allow-listed API path, copies cookies in both
  directions, and sets a trusted `Origin` for the backend check. It strips
  `X-Forwarded-For`, `X-Real-IP` and `Forwarded` from browser requests unless
  `TRUST_FORWARDED_HEADERS=1` is set behind a trusted proxy.
- The API records the socket peer as the client address. `X-Forwarded-For` is read
  only when the peer matches `TRUSTED_PROXY_CIDRS`, and then the right-most
  untrusted hop is used. The default (empty) ignores the header entirely, so a
  client cannot forge the address written to audit records or used in rate-limit
  keys.
- Custom tenant domains only route traffic once their DNS challenge is verified.
  An unverified hostname attached to a tenant resolves to `404`.
- `/docs`, `/redoc` and `/openapi.json` are disabled in production unless
  `EXPOSE_API_DOCS=true`; the schema maps the entire administrative surface.
- Security headers: `nosniff`, `X-Frame-Options: DENY`, strict referrer policy, a
  restrictive permissions policy, COOP `same-origin`, and HSTS in production.
- Request bodies larger than 2 MB are rejected before parsing, including chunked
  bodies that omit `Content-Length`.
- Production startup refuses the seeded demo password, demo seeding, HTTP public URLs,
  default development database passwords, and a missing SMTP transport.

## Rate limiting

`app/core/rate_limit.py` uses Redis fixed windows shared across replicas, with a bounded
in-process fallback if Redis is unavailable. Sensitive endpoints have their own limits:

| Endpoint | Limit |
| --- | --- |
| Sign-in per IP | 40 / 5 min |
| Sign-in per IP + email pair | 10 / 5 min |
| MFA verification per IP | 20 / 5 min |
| Invitation acceptance per IP | 20 / 5 min |
| Password reset per IP | 10 / 15 min |
| Password reset per email | 5 / 15 min |

## Secrets

- API credential secrets are prefixed (`bsk_…`), hashed with SHA-256 before storage, and
  only the display prefix is retained. The full secret is shown once.
- Refresh tokens, invitation tokens, reset tokens and recovery codes are stored hashed.
- Integration secrets that must be recoverable are encrypted with Fernet using
  `ENCRYPTION_KEY`. The key is validated at boot and must be supplied through a secret
  manager outside development.
- JWT private keys are never committed. Development keys live under `backend/keys/`
  (ignored by git); managed environments inject PEM material or mount a secret.
- Logs include request ids, method, path, status and duration. They do not include
  passwords, tokens, cookies or secret values.

## Audit

`audit_logs` stores actor id/email, action, resource type/id, scope, outcome, IP, user
agent, request id, metadata and timestamp. Denied and failed attempts are recorded.

Append-only enforcement:

1. The runtime role has `SELECT` and `INSERT` only; `UPDATE`, `DELETE` and `TRUNCATE` are
   revoked.
2. Triggers reject mutation even for the table owner.
3. No API route updates or deletes audit records.

## Tenant isolation testing

`backend/tests/test_isolation.py` authenticates as a tenant user and attempts to read
another tenant's records through every exposed path. The suite runs as the non-owner
runtime role against a disposable database, so a policy regression fails the tests
rather than being masked by an owner connection.

## Threat model summary

| Threat | Mitigation |
| --- | --- |
| Stolen access token | Short TTL, session revocation, optional MFA |
| Stolen refresh token | Rotation, reuse detection, session family revocation |
| Password brute force | Argon2id, per-IP and per-pair limits, lockout |
| Cross-tenant IDOR | Server-side scope resolution plus RLS |
| Privilege escalation | Permission-gated routes, immutable system roles, audited grants |
| Cookie CSRF | Origin check on unsafe cookie requests, same-site cookies |
| Secret leakage | Hash/encrypt at rest, one-time reveal, redacted logs |
| Audit tampering | Revoked privileges, triggers, append-only API |
| Quota bypass | Enforcement inside the usage transaction, not in the UI |
| Authorization code interception | S256 PKCE, exact redirect matching, single-use codes |
| Code or refresh replay | Hashed storage, row-locked consumption, family revocation |
| Cross-tenant login | Tenant hint re-validated against application and active membership |
| Token confusion | Separate `typ`/`aud` for console, app and service tokens |
| Stolen client secret | Hashed at rest, one-time reveal, rotation and client disable kill switch |
| Open redirect via `return_to` | Console bridge accepts only a relative `/v1/oauth/authorize` path and a configured API origin |

## Known limitations

- Social sign-in (Google, Microsoft, GitHub) is not implemented. The OIDC gateway is
  first-party only: every login client belongs to an application this company operates.
- The platform does not act as a payment processor. Invoice and payment records are
  internal state; a provider integration is an extension point.
- Webhook delivery is modelled but not scheduled in this increment.
- `password_history` is stored and editable but not enforced: there is no password
  history table yet, so reuse checks only cover the current password.
- The console's page-level permission check is client side and cosmetic; the API is
  the enforcement point. A user who deep-links to a hidden page is redirected, but
  their browser never receives data they may not read.
- The console ships framing, sniffing, referrer and permissions headers but no
  nonce-based CSP, because Next.js inlines a bootstrap script. Adding a strict CSP
  requires a per-request nonce and is left for a follow-up.
- The development seed uses a known demo password. It must never be used outside local
  development.
- Rate limiting falls back to per-process memory if Redis is unreachable; this weakens
  global limits until Redis recovers.
