"use client";

import { useState } from "react";
import { LockKeyhole, ShieldCheck } from "lucide-react";

import { Select } from "@adem.rejeb/ui";

import { CodeTabs } from "@/components/code-tabs";
import { CopyButton, CopyValue } from "@/components/copy-value";

export interface LoginClientView {
  id: string;
  name: string;
  client_id: string;
  client_type: "confidential" | "public" | string;
  redirect_uris: string[];
  post_logout_redirect_uris: string[];
  scopes: string[];
  pkce_required: boolean;
  status: string;
}

export function IntegrationPanel({
  issuer,
  productName,
  clients,
  sampleTenant,
}: {
  issuer: string;
  productName: string;
  clients: LoginClientView[];
  sampleTenant: string | null;
}) {
  const [selected, setSelected] = useState(0);
  const client = clients[selected] ?? clients[0];
  if (!client) return null;

  const redirectUri = client.redirect_uris[0] ?? "https://your-app.example.com/auth/callback";
  const postLogoutUri = client.post_logout_redirect_uris[0] ?? "https://your-app.example.com/";
  const scopes = client.scopes.length ? client.scopes.join(" ") : "openid profile email";
  const tenantHint = sampleTenant ?? "your-client-slug";
  const confidential = client.client_type === "confidential";

  const env = [
    `BASACORE_ISSUER=${issuer}`,
    `BASACORE_CLIENT_ID=${client.client_id}`,
    ...(confidential
      ? [`BASACORE_CLIENT_SECRET=__paste_secret_from_creation__`]
      : []),
    `BASACORE_REDIRECT_URI=${redirectUri}`,
    `BASACORE_SCOPES=${scopes}`,
    `BASACORE_TENANT=${tenantHint}`,
    `BASACORE_API_URL=${issuer}`,
  ].join("\n");

  const hostedLogin = `// app/auth/login/route.ts — hosted login (recommended)
import { createHash, randomBytes } from "node:crypto";
import { NextResponse } from "next/server";

export async function GET() {
  const verifier = randomBytes(32).toString("base64url");
  const challenge = createHash("sha256").update(verifier).digest("base64url");
  const state = randomBytes(16).toString("base64url");
  const nonce = randomBytes(16).toString("base64url");

  // Store verifier, state and nonce in an httpOnly, secure cookie or your
  // server-side session. They must be checked again on the callback.
  const authorize = new URL(\`\${process.env.BASACORE_ISSUER}/v1/oauth/authorize\`);
  authorize.search = new URLSearchParams({
    response_type: "code",
    client_id: process.env.BASACORE_CLIENT_ID!,
    redirect_uri: process.env.BASACORE_REDIRECT_URI!,
    scope: process.env.BASACORE_SCOPES!,
    state,
    nonce,
    code_challenge: challenge,
    code_challenge_method: "S256",
    tenant: process.env.BASACORE_TENANT!,
  }).toString();

  const response = NextResponse.redirect(authorize);
  response.cookies.set("oidc_verifier", verifier, { httpOnly: true, secure: true, sameSite: "lax" });
  response.cookies.set("oidc_state", state, { httpOnly: true, secure: true, sameSite: "lax" });
  response.cookies.set("oidc_nonce", nonce, { httpOnly: true, secure: true, sameSite: "lax" });
  return response;
}`;

  const tokenExchange = `// app/auth/callback/route.ts — verify state, exchange the code, verify the ID token
import { jwtVerify, createRemoteJWKSet } from "jose";

const issuer = process.env.BASACORE_ISSUER!;
const clientId = process.env.BASACORE_CLIENT_ID!;
const clientSecret = process.env.BASACORE_CLIENT_SECRET!;
const redirectUri = process.env.BASACORE_REDIRECT_URI!;

// 1. Reject the request when state does not match the value you stored.
// 2. Exchange the one-time code server-side.
const basic = Buffer.from(\`\${clientId}:\${clientSecret}\`).toString("base64");
const tokenResponse = await fetch(\`\${issuer}/v1/oauth/token\`, {
  method: "POST",
  headers: {
    "content-type": "application/x-www-form-urlencoded",
    authorization: \`Basic \${basic}\`,
  },
  body: new URLSearchParams({
    grant_type: "authorization_code",
    code,
    redirect_uri: redirectUri,
    code_verifier: verifier,
  }),
});
if (!tokenResponse.ok) throw new Error("Token exchange failed");
const tokens = await tokenResponse.json();

// 3. Verify signature, issuer, audience, expiry and nonce before trusting claims.
const jwks = createRemoteJWKSet(new URL(\`\${issuer}/.well-known/jwks.json\`));
const { payload } = await jwtVerify(tokens.id_token, jwks, {
  issuer,
  audience: clientId,
});
if (payload.nonce !== nonce) throw new Error("Nonce mismatch");

// payload.sub, payload.tenant_id, payload.roles, payload.permissions are now trusted.`;

  const embeddedLogin = `// Server only. Never call this endpoint from a browser.
const response = await fetch(\`\${process.env.BASACORE_ISSUER}/v1/oauth/embedded/login\`, {
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify({
    client_id: process.env.BASACORE_CLIENT_ID,
    client_secret: process.env.BASACORE_CLIENT_SECRET,
    tenant: process.env.BASACORE_TENANT,
    email,
    password,
    scope: process.env.BASACORE_SCOPES,
  }),
});
const result = await response.json();

if (result.status === "mfa_required") {
  // Ask for the one-time code, then POST /v1/oauth/embedded/mfa.
} else if (result.status === "authenticated") {
  // result.access_token, result.refresh_token, result.id_token
}`;

  return (
    <div className="stack" style={{ gap: 18 }}>
      <div className="integration-callout">
        <div>
          <strong>Use these values in your codebase</strong>
          <p>
            They are safe to copy into {productName}. The client secret is never stored in
            clear and is only shown once when a client is created or rotated.
          </p>
        </div>
        <CopyButton value={env} label="Copy .env" />
      </div>

      {clients.length > 1 ? (
        <div className="integration-picker">
          <label className="kv-label" htmlFor="login-client">
            Sign-in client
          </label>
          <Select
            id="login-client"
            value={String(selected)}
            onChange={(event) => setSelected(Number(event.target.value))}
          >
            {clients.map((item, index) => (
              <option key={item.id} value={index}>
                {item.name} · {item.client_type}
              </option>
            ))}
          </Select>
        </div>
      ) : null}

      <div className="copy-grid">
        <CopyValue
          label="Issuer"
          value={issuer}
          description="Exact iss value and the base of every endpoint below."
        />
        <CopyValue
          label="Client ID"
          value={client.client_id}
          description="Public identifier. Safe to ship in a browser for public clients."
        />
        <CopyValue
          label="Redirect URI"
          value={redirectUri}
          description="Must match exactly, including scheme, host, port and path."
        />
        <CopyValue
          label="Workspace hint"
          value={tenantHint}
          description="Sent as tenant= on authorize. Basacore checks it against membership."
        />
        <CopyValue
          label="Scopes"
          value={scopes}
          description="openid is required. offline_access enables refresh tokens."
        />
        <CopyValue
          label="Client type"
          value={`${client.client_type}${client.pkce_required ? " · PKCE required" : ""}`}
          description={
            confidential
              ? "Keep the secret on the server; exchange the code server-side."
              : "No secret. Use S256 PKCE and keep tokens in memory where possible."
          }
        />
      </div>

      <div className="section-subhead">
        <h3>Endpoints</h3>
        <span className="small muted">OpenID discovery is published at the first URL.</span>
      </div>
      <div className="copy-grid">
        <CopyValue label="Discovery" value={`${issuer}/.well-known/openid-configuration`} />
        <CopyValue label="JWKS" value={`${issuer}/.well-known/jwks.json`} />
        <CopyValue label="Authorize" value={`${issuer}/v1/oauth/authorize`} />
        <CopyValue label="Token" value={`${issuer}/v1/oauth/token`} />
        <CopyValue label="Userinfo" value={`${issuer}/v1/oauth/userinfo`} />
        <CopyValue label="Revoke" value={`${issuer}/v1/oauth/revoke`} />
        <CopyValue label="Logout" value={`${issuer}/v1/oauth/logout`} />
      </div>

      <div className="section-subhead">
        <h3>Environment variables</h3>
        <span className="small muted">Copy this block into your deployment environment.</span>
      </div>
      <div className="code-tabs">
        <div className="code-tabs__bar">
          <div className="code-tabs__labels">
            <span className="code-tabs__tab is-active">.env</span>
          </div>
          <CopyButton value={env} label="Copy" />
        </div>
        <pre className="code-block code-block--flush">{env}</pre>
      </div>

      <div className="section-subhead">
        <h3>Working examples</h3>
        <span className="small muted">Next.js route handlers plus a server-only embedded flow.</span>
      </div>
      <CodeTabs
        samples={[
          {
            label: "Hosted sign-in",
            code: hostedLogin,
            hint: "Register the callback URL on the sign-in client before using this flow.",
          },
          {
            label: "Token exchange",
            code: tokenExchange,
            hint: "confidential clients only. Public clients use PKCE without a secret.",
          },
          {
            label: "Embedded login",
            code: embeddedLogin,
            hint: "Only available to confidential clients and must run on your server.",
          },
        ]}
      />

      <div className="integration-notes">
        <div className="integration-note">
          <ShieldCheck size={16} />
          <div>
            <strong>Server-side authorization</strong>
            <span>
              Treat roles and permissions from userinfo as a snapshot. Re-check sensitive
              actions against the stored grant.
            </span>
          </div>
        </div>
        <div className="integration-note">
          <LockKeyhole size={16} />
          <div>
            <strong>Secret handling</strong>
            <span>
              Never expose the client secret to the browser. Rotate it in the console and
              update your secret manager in the same change window.
            </span>
          </div>
        </div>
      </div>

      <p className="copy-value__hint">
        Full reference: <code>docs/INTEGRATION.md</code> · Post-logout redirect:{" "}
        <code>{postLogoutUri}</code>
      </p>
    </div>
  );
}
