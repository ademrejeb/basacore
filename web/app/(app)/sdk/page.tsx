import Link from "next/link";
import { BookOpen, KeyRound, LockKeyhole, RefreshCw, Terminal } from "lucide-react";

import { Badge } from "@adem.rejeb/ui";

import { EmptyState } from "@/components/pagination";
import { apiFetch } from "@/lib/api";
import type { Application, Page } from "@/lib/types";

export const metadata = { title: "SDK & API" };

const SCOPES: Array<{ key: string; description: string }> = [
  { key: "app:config.read", description: "Read effective application and workspace settings." },
  { key: "app:tenant.resolve", description: "Resolve a workspace by id, short name or verified domain." },
  { key: "app:identity.read", description: "Read workspace membership and identity summaries." },
  {
    key: "app:authorization.check",
    description: "Check a user permission inside a workspace.",
  },
  { key: "app:usage.write", description: "Report metered usage, subject to plan limits." },
  { key: "app:audit.write", description: "Append application events to the activity log." },
];

const SAMPLE = `import { BasacoreClient } from "@basacore/sdk";

const basacore = new BasacoreClient({
  baseUrl: process.env.BASACORE_API_URL!,
  clientId: process.env.BASACORE_CLIENT_ID!,
  clientSecret: process.env.BASACORE_CLIENT_SECRET!,
});

// Resolve a tenant without trusting a client-supplied tenant id.
const tenant = await basacore.resolveTenant({ slug: "lumen-health" });

// Server-side authorization check for the signed-in end user.
const decision = await basacore.checkAuthorization({
  userId: currentUser.id,
  tenantId: tenant.id,
  permission: "projects.read",
});

if (!decision.allowed) throw new Error("Forbidden");

// Report usage; the API rejects writes that would exceed quota.
await basacore.reportUsage({
  tenantId: tenant.id,
  metric: "api_calls",
  value: 25,
  idempotencyKey: requestId,
});`;

const TOKEN_SAMPLE = `curl -sS http://localhost:8000/v1/oauth/token \\
  -H "content-type: application/x-www-form-urlencoded" \\
  -d grant_type=client_credentials \\
  -d client_id="$BASACORE_CLIENT_ID" \\
  -d client_secret="$BASACORE_CLIENT_SECRET"
# => { "access_token": "...", "token_type": "Bearer", "expires_in": 3600 }`;

export default async function SdkPage() {
  const applications = await apiFetch<Page<Application>>("/v1/applications?limit=100");

  return (
    <div className="page-stack">
      <div className="page-heading">
        <div>
          <span className="eyebrow">Integration</span>
          <h1>SDK &amp; integration API</h1>
          <p>
            Server-to-server access uses the OAuth 2.0 client-credentials grant. A
            credential is bound to one application, and every endpoint rejects requests
            outside that application&apos;s workspaces.
          </p>
        </div>
        <div className="page-heading__actions">
          <Badge variant="info" size="sm">
            OpenAPI 3.1 · ~100 operations
          </Badge>
          <Link className="ui-button ui-button--outline" href="/applications">
            Manage applications
          </Link>
        </div>
      </div>

      <section className="detail-grid">
        <div className="section-card">
          <div className="section-card__head">
            <div>
              <h2>Install the client</h2>
              <p>Typed client published in this workspace.</p>
            </div>
            <Terminal size={17} />
          </div>
          <div className="section-card__body stack">
            <pre className="code-block">{`# from the repository root
cd packages/sdk-ts
npm install
npm run build

# consume from your service
npm install ../packages/sdk-ts`}</pre>
            <p className="small muted" style={{ margin: 0 }}>
              The client has no runtime dependencies. It caches tokens in memory and
              refreshes them 30 seconds before expiry.
            </p>
          </div>
        </div>

        <div className="section-card">
          <div className="section-card__head">
            <div>
              <h2>Issue a credential</h2>
              <p>One credential per environment and integration.</p>
            </div>
            <KeyRound size={17} />
          </div>
          <div className="section-card__body stack">
            <ol className="small muted" style={{ margin: 0, paddingLeft: 18, lineHeight: 1.9 }}>
              <li>Open an application from the registry.</li>
              <li>Create a credential and select only the required scopes.</li>
              <li>Copy the secret once, then store it in a secret manager.</li>
              <li>Use it from a backend service — never from a browser.</li>
            </ol>
            <div className="row small muted">
              <LockKeyhole size={14} /> Secrets are hashed at rest. Only the prefix is stored in
              clear.
            </div>
          </div>
        </div>
      </section>

      <section className="section-card">
        <div className="section-card__head">
          <div>
            <h2>Obtain an access token</h2>
            <p>Client credentials are exchanged at <span className="mono">/v1/oauth/token</span>.</p>
          </div>
          <RefreshCw size={17} />
        </div>
        <div className="section-card__body stack">
          <pre className="code-block">{TOKEN_SAMPLE}</pre>
          <p className="small muted" style={{ margin: 0 }}>
            Tokens are short-lived. The SDK requests a new token automatically and retries
            once on <span className="mono">401</span>.
          </p>
        </div>
      </section>

      <section className="section-card">
        <div className="section-card__head">
          <div>
            <h2>End-to-end example</h2>
            <p>Resolve, authorize and meter usage in a single request path.</p>
          </div>
          <BookOpen size={17} />
        </div>
        <div className="section-card__body">
          <pre className="code-block">{SAMPLE}</pre>
        </div>
      </section>

      <section className="detail-grid">
        <div className="section-card">
          <div className="section-card__head">
            <div>
              <h2>Scopes</h2>
              <p>Least privilege is enforced per credential.</p>
            </div>
            <Badge variant="outline" size="sm">
              {SCOPES.length} scopes
            </Badge>
          </div>
          <div className="section-card__body stack">
            {SCOPES.map((scope) => (
              <div className="alert-row" key={scope.key}>
                <div className="alert-row__meta">
                  <strong className="mono">{scope.key}</strong>
                  <span>{scope.description}</span>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="section-card">
          <div className="section-card__head">
            <div>
              <h2>Applications</h2>
              <p>Credentials are scoped to the application that issued them.</p>
            </div>
          </div>
          {applications.items.length === 0 ? (
            <EmptyState
              title="No applications"
              description="Register an application before issuing an integration credential."
              action={
                <Link className="ui-button ui-button--primary" href="/applications">
                  Register an application
                </Link>
              }
            />
          ) : (
            <div className="section-card__body stack">
              {applications.items.map((application) => (
                <div className="alert-row" key={application.id}>
                  <div className="alert-row__meta">
                    <strong>{application.name}</strong>
                    <span className="mono">{application.slug}</span>
                  </div>
                  <Link className="pagination__link" href={`/applications/${application.id}`}>
                    Manage
                  </Link>
                </div>
              ))}
            </div>
          )}
        </div>
      </section>

      <section className="section-card">
        <div className="section-card__head">
          <div>
            <h2>Guarantees</h2>
            <p>What the integration API promises to your application.</p>
          </div>
        </div>
        <div className="section-card__body stack">
          {[
            "Credentials are bound to an application; cross-application workspace access returns 404.",
            "Every metered write is checked against the workspace plan limits in the same transaction.",
            "Activity log entries are append-only and carry actor, scope, request id and outcome.",
            "Workspace settings are versioned and immutable; effective values merge application and environment settings.",
            "Feature flags are evaluated per workspace with explicit overrides.",
            "Errors use a stable envelope: { error: { code, message, details, request_id } }.",
          ].map((line) => (
            <div className="alert-row" key={line}>
              <div className="alert-row__meta">
                <strong>{line}</strong>
              </div>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
