# @basacore/sdk

Typed TypeScript client for the Basacore integration API. It uses the OAuth 2.0
client-credentials grant with a credential issued from the Basacore dashboard and
caches tokens until shortly before they expire.

```ts
import { BasacoreClient } from "@basacore/sdk";

const basacore = new BasacoreClient({
  baseUrl: "http://localhost:8000",
  clientId: process.env.BASACORE_CLIENT_ID!,
  clientSecret: process.env.BASACORE_CLIENT_SECRET!,
});

const tenants = await basacore.listTenants({ limit: 20 });
const tenant = await basacore.resolveTenant({ slug: "atlas" });

const context = await basacore.getTenantConfig(tenant.id);
const allowed = await basacore.checkAuthorization({
  userId: "a4f6…",
  tenantId: tenant.id,
  permission: "projects.read",
});

await basacore.reportUsage({
  tenantId: tenant.id,
  metric: "api_calls",
  value: 25,
  idempotencyKey: `request-${crypto.randomUUID()}`,
});

await basacore.writeAudit({
  action: "project.created",
  resourceType: "project",
  resourceId: "prj_123",
  tenantId: tenant.id,
});
```

## Behaviour

- Tokens are cached in memory and refreshed 30 seconds before expiry.
- A `401` response invalidates the cached token and retries the request once.
- Failed calls throw `BasacoreApiError` with `status`, `code`, `details` and
  `requestId` where the API supplies them.
- Every request times out after 15 seconds by default (`timeoutMs`).
- No secrets are logged or persisted.

## Development

```bash
npm install
npm test
npm run build
```
