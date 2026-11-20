import Link from "next/link";
import { notFound } from "next/navigation";
import { ArrowRight, ShieldCheck } from "lucide-react";

import { Badge, Checkbox, FormField, Input, Select, Textarea } from "@adem.rejeb/ui";

import {
  disableApplicationClientAction,
  revokeCredentialAction,
  rotateApplicationClientSecretAction,
  updateApplicationClientAction,
} from "@/app/actions";
import { ActionDialog } from "@/components/action-dialog";
import { CodeTabs } from "@/components/code-tabs";
import { CopyValue } from "@/components/copy-value";
import { EmptyState } from "@/components/pagination";
import { CreateCredentialDialog, CreateLoginClientDialog } from "@/components/product-dialogs";
import { ProductHeader } from "@/components/product-header";
import { StatusBadge } from "@/components/status-badge";
import { IntegrationPanel } from "@/components/integration-panel";
import { apiFetch, ApiError } from "@/lib/api";
import { dateTime, titleCase } from "@/lib/format";
import type { ApplicationDetail, Page, Tenant } from "@/lib/types";

export const metadata = { title: "Setup · Application" };

const CLIENT_SCOPES = ["openid", "profile", "email", "offline_access"];

type Params = Promise<{ id: string }>;

function publicIssuer(): string {
  return (
    process.env.PUBLIC_API_BASE_URL ??
    process.env.API_BASE_URL ??
    "http://localhost:8000"
  ).replace(/\/$/, "");
}

export default async function ApplicationIntegrationPage({ params }: { params: Params }) {
  const { id } = await params;

  let application: ApplicationDetail;
  let tenants: Page<Tenant>;
  try {
    [application, tenants] = await Promise.all([
      apiFetch<ApplicationDetail>(`/v1/applications/${id}`),
      apiFetch<Page<Tenant>>(`/v1/tenants?application_id=${id}&limit=20`),
    ]);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) notFound();
    throw error;
  }

  const issuer = publicIssuer();
  const activeClients = (application.clients ?? []).filter((client) => client.status === "active");
  const activeCredentials = application.credentials.filter(
    (credential) => credential.status === "active" && !credential.revoked_at,
  );
  const primaryCredential = activeCredentials[0];
  const sampleTenant =
    tenants.items.find((tenant) => ["active", "trial"].includes(tenant.status))?.slug ??
    tenants.items[0]?.slug ??
    null;
  const latestConfig = application.config_history[0];
  const configEntries = Object.entries(latestConfig?.config ?? {});

  const apiTokenSample = `curl -sS ${issuer}/v1/oauth/token \\
  -H "content-type: application/x-www-form-urlencoded" \\
  -d grant_type=client_credentials \\
  -d client_id="$BASACORE_CLIENT_ID" \\
  -d client_secret="$BASACORE_CLIENT_SECRET"

# => { "access_token": "...", "token_type": "Bearer", "expires_in": 3600 }`;

  const sdkSample = `import { BasacoreClient } from "@basacore/sdk";

const basacore = new BasacoreClient({
  baseUrl: process.env.BASACORE_API_URL!,
  clientId: process.env.BASACORE_CLIENT_ID!,
  clientSecret: process.env.BASACORE_CLIENT_SECRET!,
});

const tenant = await basacore.resolveTenant({ slug: "${sampleTenant ?? "your-client-slug"}" });
const context = await basacore.getTenantConfig(tenant.id);

if (!context.features["new_checkout"]) {
  // Keep the old checkout path.
}`;

  return (
    <div className="page-stack">
      <ProductHeader application={application} active="integration" />

      <section className="section-card">
        <div className="section-card__head">
          <div>
            <h2>End-user sign-in</h2>
            <p>
              Your application sends people to Basacore, then checks the returned ID token.
              Everything your code needs is collected here.
            </p>
          </div>
          <div className="row" style={{ gap: 8 }}>
            <Badge variant={activeClients.length ? "success" : "warning"} size="sm">
              {activeClients.length ? "Sign-in ready" : "Sign-in not set up"}
            </Badge>
            <CreateLoginClientDialog applicationId={application.id} triggerVariant="outline" />
          </div>
        </div>
        <div className="section-card__body">
          {activeClients.length === 0 ? (
            <EmptyState
              title="Set up sign-in first"
              description="A sign-in client gives your application a client ID, redirect URIs and scopes. Create one here and copy the values immediately."
              action={<CreateLoginClientDialog applicationId={application.id} />}
            />
          ) : (
            <IntegrationPanel
              issuer={issuer}
              productName={application.name}
              sampleTenant={sampleTenant}
              clients={activeClients.map((client) => ({
                id: client.id,
                name: client.name,
                client_id: client.client_id,
                client_type: client.client_type,
                redirect_uris: client.redirect_uris,
                post_logout_redirect_uris: client.post_logout_redirect_uris,
                scopes: client.scopes,
                pkce_required: client.pkce_required,
                status: client.status,
              }))}
            />
          )}
        </div>
      </section>

      <section className="section-card">
        <div className="section-card__head">
          <div>
            <h2>Sign-in clients</h2>
            <p>Each sign-in client belongs to this application. Redirect URIs must match exactly.</p>
          </div>
        </div>
        {(application.clients ?? []).length === 0 ? (
          <EmptyState
            title="No sign-in clients"
            description="Set up a sign-in client before pointing the application at the sign-in endpoint."
          />
        ) : (
          <div className="section-card__body stack">
            {(application.clients ?? []).map((client) => (
              <div className="alert-row" key={client.id}>
                <div className="alert-row__meta">
                  <strong>
                    {client.name} <span className="tag-mono">{titleCase(client.client_type)}</span>
                    {client.pkce_required ? <span className="tag-mono">PKCE</span> : null}
                  </strong>
                  <span className="mono">{client.client_id}</span>
                  <span>
                    {client.redirect_uris.length
                      ? client.redirect_uris.join(" · ")
                      : "No redirect URIs"}
                  </span>
                  <span className="small muted">
                    Scopes: {client.scopes.join(" ")} · last used {dateTime(client.last_used_at)}
                  </span>
                </div>
                <div className="row" style={{ gap: 8 }}>
                  <StatusBadge status={client.status} />
                  <ActionDialog
                    label="Edit"
                    triggerVariant="outline"
                    triggerSize="sm"
                    title={`Edit ${client.name}`}
                    description="Disabling a sign-in client ends every active session created through it."
                    action={updateApplicationClientAction}
                    submitLabel="Save sign-in client"
                    size="lg"
                  >
                    <input type="hidden" name="id" value={application.id} />
                    <input type="hidden" name="client_id" value={client.client_id} />
                    <FormField label="Name" required>
                      <Input name="name" defaultValue={client.name} required />
                    </FormField>
                    <FormField label="Status">
                      <Select name="status" defaultValue={client.status}>
                        <option value="active">Active</option>
                        <option value="disabled">Disabled</option>
                      </Select>
                    </FormField>
                    <FormField label="Redirect URIs" description="One per line, exact match required." required>
                      <Textarea
                        name="redirect_uris"
                        rows={3}
                        defaultValue={client.redirect_uris.join("\n")}
                        required
                      />
                    </FormField>
                    <FormField label="Post-logout redirect URIs" description="One per line.">
                      <Textarea
                        name="post_logout_redirect_uris"
                        rows={2}
                        defaultValue={client.post_logout_redirect_uris.join("\n")}
                      />
                    </FormField>
                    <FormField label="Allowed browser origins" description="One per line, optional.">
                      <Textarea
                        name="allowed_origins"
                        rows={2}
                        defaultValue={client.allowed_origins.join("\n")}
                      />
                    </FormField>
                    <FormField label="Scopes">
                      <div className="stack" style={{ gap: 8 }}>
                        {CLIENT_SCOPES.map((scope) => (
                          <Checkbox
                            key={scope}
                            name="scopes"
                            value={scope}
                            defaultChecked={client.scopes.includes(scope)}
                            label={<span className="mono">{scope}</span>}
                          />
                        ))}
                      </div>
                    </FormField>
                    <FormField label="PKCE">
                      <Checkbox
                        name="pkce_required"
                        label="Require S256 PKCE"
                        defaultChecked={client.pkce_required}
                      />
                    </FormField>
                  </ActionDialog>
                  {client.client_type === "confidential" && client.status === "active" ? (
                    <ActionDialog
                      label="Rotate secret"
                      triggerVariant="outline"
                      triggerSize="sm"
                      title={`Rotate secret for ${client.name}`}
                      description="The previous secret stops working immediately. Existing sessions continue until signed out."
                      action={rotateApplicationClientSecretAction}
                      submitLabel="Rotate secret"
                      closeOnSuccess={false}
                    >
                      <input type="hidden" name="id" value={application.id} />
                      <input type="hidden" name="client_id" value={client.client_id} />
                      <p className="small muted" style={{ margin: 0 }}>
                        Update the application&apos;s secret manager in the same change window.
                      </p>
                    </ActionDialog>
                  ) : null}
                  {client.status === "active" ? (
                    <ActionDialog
                      label="Disable"
                      triggerVariant="ghost"
                      triggerSize="sm"
                      title={`Disable ${client.name}`}
                      description="The sign-in client stops issuing tokens and every session it created ends immediately."
                      action={disableApplicationClientAction}
                      submitLabel="Disable sign-in client"
                      destructive
                    >
                      <input type="hidden" name="id" value={application.id} />
                      <input type="hidden" name="client_id" value={client.client_id} />
                    </ActionDialog>
                  ) : null}
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="section-card">
        <div className="section-card__head">
          <div>
            <h2>Server API access</h2>
            <p>
              Your backend uses a credential to look up workspaces, read settings and
              check permissions. Keep it out of the browser.
            </p>
          </div>
          <CreateCredentialDialog applicationId={application.id} triggerVariant="outline" />
        </div>
        <div className="section-card__body stack">
          {activeCredentials.length === 0 ? (
            <EmptyState
              title="No API credential"
              description="Create a credential to let the application backend call the Basacore API."
            />
          ) : (
            <div className="copy-grid">
              <CopyValue
                label="API base URL"
                value={issuer}
                description="Server-to-server calls use the same issuer origin."
              />
              <CopyValue
                label="Token endpoint"
                value={`${issuer}/v1/oauth/token`}
                description="OAuth 2.0 client-credentials grant."
              />
              <CopyValue
                label="Client ID"
                value={primaryCredential?.id ?? ""}
                description="The credential id is the OAuth client_id."
              />
              <CopyValue
                label="Scopes"
                value={(primaryCredential?.scopes ?? []).join(" ")}
                description="Only the scopes granted to this credential are accepted."
              />
            </div>
          )}

          {activeCredentials.length > 0 ? (
            <>
              <div className="data-table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th scope="col">Credential</th>
                      <th scope="col">Prefix</th>
                      <th scope="col">Scopes</th>
                      <th scope="col">Status</th>
                      <th scope="col">Last used</th>
                      <th scope="col" className="align-end">
                        Actions
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {application.credentials.map((credential) => (
                      <tr key={credential.id}>
                        <td>
                          <span className="cell-primary">{credential.name}</span>
                          <span className="cell-sub mono">{credential.id}</span>
                        </td>
                        <td className="mono">{credential.key_prefix}…</td>
                        <td>
                          <div className="pill-list">
                            {credential.scopes.map((scope) => (
                              <span className="tag-mono" key={scope}>
                                {scope}
                              </span>
                            ))}
                          </div>
                        </td>
                        <td>
                          <StatusBadge
                            status={credential.revoked_at ? "revoked" : credential.status}
                          />
                        </td>
                        <td className="muted">{dateTime(credential.last_used_at)}</td>
                        <td className="align-end">
                          {credential.revoked_at ? (
                            <span className="small muted">Revoked</span>
                          ) : (
                            <ActionDialog
                              label="Revoke"
                              triggerVariant="ghost"
                              triggerSize="sm"
                              title={`Revoke ${credential.name}`}
                              description="The credential stops issuing tokens immediately. This cannot be undone."
                              action={revokeCredentialAction}
                              submitLabel="Revoke credential"
                              destructive
                            >
                              <input type="hidden" name="id" value={application.id} />
                              <input type="hidden" name="credential_id" value={credential.id} />
                            </ActionDialog>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <CodeTabs
                samples={[
                  { label: "Token request", code: apiTokenSample },
                  {
                    label: "TypeScript SDK",
                    code: sdkSample,
                    hint: "The SDK caches the service token in memory and retries once on 401.",
                  },
                ]}
              />
            </>
          ) : null}
        </div>
      </section>

      <section className="detail-grid">
        <div className="section-card">
          <div className="section-card__head">
            <div>
              <h2>Settings keys</h2>
              <p>Values your application reads at runtime for this application.</p>
            </div>
            {latestConfig ? (
              <Badge variant="outline" size="sm">
                version {latestConfig.version}
              </Badge>
            ) : null}
          </div>
          <div className="section-card__body">
            {configEntries.length === 0 ? (
              <EmptyState
                title="No settings published"
                description="Publish a settings version from the overview page to show keys here."
              />
            ) : (
              <div className="stack" style={{ gap: 10 }}>
                {configEntries.map(([key, value]) => (
                  <CopyValue
                    key={key}
                    label={key}
                    value={typeof value === "string" ? value : JSON.stringify(value)}
                  />
                ))}
              </div>
            )}
          </div>
        </div>

        <div className="section-card">
          <div className="section-card__head">
            <div>
              <h2>Feature flag keys</h2>
              <p>Keys your code can check after resolving the workspace.</p>
            </div>
            <Link className="small muted" href={`/applications/${application.id}`}>
              Manage flags
            </Link>
          </div>
          <div className="section-card__body">
            {application.flags.length === 0 ? (
              <p className="small muted" style={{ margin: 0 }}>
                No flags defined yet.
              </p>
            ) : (
              <div className="stack" style={{ gap: 10 }}>
                {application.flags.map((flag) => (
                  <div className="alert-row" key={flag.id}>
                    <div className="alert-row__meta">
                      <strong className="mono">{flag.key}</strong>
                      <span>
                        {flag.name} · rollout {flag.rollout_percentage}%
                      </span>
                    </div>
                    <StatusBadge status={flag.enabled ? "enabled" : "inactive"} />
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </section>

      <section className="section-card">
        <div className="section-card__head">
          <div>
            <h2>Security checklist</h2>
            <p>What the Basacore gateway already enforces, and what your code must do.</p>
          </div>
        </div>
        <div className="section-card__body stack">
          {[
            {
              title: "Verify the ID token",
              body: "Check RS256 signature, issuer, audience, expiry and nonce. Never trust claims from an unverified token.",
              enforced: false,
            },
            {
              title: "Validate redirect URIs",
              body: "Basacore matches redirect URIs exactly and rejects unregistered values before redirecting.",
              enforced: true,
            },
            {
              title: "Check membership",
              body: "The workspace hint is only accepted when the user holds an active membership in that workspace.",
              enforced: true,
            },
            {
              title: "Authorize on the server",
              body: "Use the permission snapshot for UI hints, but re-check sensitive actions against the stored grant.",
              enforced: false,
            },
            {
              title: "Keep secrets server-side",
              body: "Confidential client secrets never belong in browser bundles. Public clients use PKCE without a secret.",
              enforced: false,
            },
          ].map((item) => (
            <div className="alert-row" key={item.title}>
              <div className="alert-row__meta">
                <strong>{item.title}</strong>
                <span>{item.body}</span>
              </div>
              <Badge variant={item.enforced ? "success" : "outline"} size="sm">
                {item.enforced ? "Enforced by Basacore" : "Your responsibility"}
              </Badge>
            </div>
          ))}
          <div className="row small muted">
            <ShieldCheck size={14} /> Tokens have separated audiences: app, console and service
            tokens cannot be substituted for each other.
            <ArrowRight size={14} />
            <Link href="/audit">Review activity</Link>
          </div>
        </div>
      </section>
    </div>
  );
}
