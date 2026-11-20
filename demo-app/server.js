/**
 * Atlas CRM — external demo application for the Basacore auth gateway.
 *
 * This app is deliberately dependency-free: it is the smallest realistic
 * confidential client. It runs the OIDC authorization-code flow with PKCE,
 * exchanges the code server-side, verifies the ID token against the published
 * JWKS, calls userinfo, and shows exactly what the application receives.
 *
 * It also demonstrates the embedded (server-side) login path and RP-initiated
 * logout through the Basacore end-session endpoint.
 */

"use strict";

const crypto = require("node:crypto");
const http = require("node:http");

const PORT = Number(process.env.PORT || 3003);
const API_BASE = trimSlash(process.env.BASACORE_API_BASE_URL || "http://localhost:8000");
const API_INTERNAL_BASE = trimSlash(
  process.env.BASACORE_INTERNAL_API_BASE_URL || API_BASE,
);
const PUBLIC_BASE = trimSlash(process.env.PUBLIC_BASE_URL || "http://localhost:3003");
const CLIENT_ID = process.env.BASACORE_CLIENT_ID || "basa_demo_atlas_web";
const CLIENT_SECRET = process.env.BASACORE_CLIENT_SECRET || "bsc_demo_atlas_web_secret_2026";
const TENANT_HINT = process.env.BASACORE_TENANT || "northwind-sales";
const ISSUER = process.env.BASACORE_ISSUER || API_BASE;
const REDIRECT_URI = `${PUBLIC_BASE}/auth/callback`;
const SCOPE = "openid profile email offline_access";
const DEMO_EMAIL = process.env.DEMO_EMAIL || "emma.wilson@northwind.dev";
const DEMO_PASSWORD = process.env.DEMO_PASSWORD || "Adm1n!Basa#2026";
const SECURE_COOKIES = PUBLIC_BASE.startsWith("https://");

const SESSION_COOKIE = "atlas_sid";
const STATE_TTL_MS = 10 * 60 * 1000;
const SESSION_TTL_MS = 8 * 60 * 60 * 1000;

const pending = new Map();
const pendingMfa = new Map();
const sessions = new Map();
let jwksCache = { at: 0, keys: [] };

function trimSlash(value) {
  return value.replace(/\/+$/, "");
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function base64url(buffer) {
  return Buffer.from(buffer).toString("base64url");
}

function pkce() {
  const verifier = base64url(crypto.randomBytes(48));
  const challenge = base64url(crypto.createHash("sha256").update(verifier).digest());
  return { verifier, challenge };
}

function randomId() {
  return crypto.randomBytes(24).toString("base64url");
}

function parseCookies(header) {
  const cookies = {};
  for (const part of String(header || "").split(";")) {
    const index = part.indexOf("=");
    if (index < 0) continue;
    cookies[part.slice(0, index).trim()] = decodeURIComponent(part.slice(index + 1).trim());
  }
  return cookies;
}

function sessionCookie(value, maxAgeSeconds) {
  const attributes = [
    `${SESSION_COOKIE}=${encodeURIComponent(value)}`,
    "Path=/",
    "HttpOnly",
    "SameSite=Lax",
    `Max-Age=${maxAgeSeconds}`,
  ];
  if (SECURE_COOKIES) attributes.push("Secure");
  return attributes.join("; ");
}

async function readForm(request) {
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > 16 * 1024) throw new Error("Request body too large.");
    chunks.push(chunk);
  }
  return new URLSearchParams(Buffer.concat(chunks).toString("utf8"));
}

async function postForm(url, params, headers = {}) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded", ...headers },
    body: new URLSearchParams(params),
  });
  const body = await response.json().catch(() => ({}));
  return { ok: response.ok, status: response.status, body };
}

async function postJson(url, payload, headers = {}) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json", ...headers },
    body: JSON.stringify(payload),
  });
  const body = await response.json().catch(() => ({}));
  return { ok: response.ok, status: response.status, body };
}

/**
 * Extract a human-readable message from either the OAuth envelope
 * (``error``/``error_description``) or the control-plane envelope
 * (``error.message``).
 */
function errorMessage(body, fallback) {
  if (body && typeof body.error_description === "string") return body.error_description;
  const error = body && body.error;
  if (typeof error === "string") return error;
  if (error && typeof error === "object" && typeof error.message === "string") return error.message;
  if (body && typeof body.message === "string") return body.message;
  return fallback;
}

async function jwks() {
  if (Date.now() - jwksCache.at < 5 * 60 * 1000 && jwksCache.keys.length) {
    return jwksCache.keys;
  }
  const response = await fetch(`${API_INTERNAL_BASE}/.well-known/jwks.json`);
  if (!response.ok) throw new Error("The JWKS endpoint is unavailable.");
  const body = await response.json();
  jwksCache = { at: Date.now(), keys: body.keys || [] };
  return jwksCache.keys;
}

/** Full RS256 verification of the ID token, exactly as a real client must do. */
async function verifyIdToken(idToken, expectedNonce) {
  const parts = String(idToken || "").split(".");
  if (parts.length !== 3) throw new Error("The ID token is malformed.");
  const header = JSON.parse(Buffer.from(parts[0], "base64url").toString("utf8"));
  const payload = JSON.parse(Buffer.from(parts[1], "base64url").toString("utf8"));
  if (header.alg !== "RS256") throw new Error("Unexpected ID token algorithm.");

  const keys = await jwks();
  const jwk = keys.find((key) => key.kid === header.kid);
  if (!jwk) throw new Error("No signing key matches the ID token.");
  const publicKey = crypto.createPublicKey({ key: jwk, format: "jwk" });
  const valid = crypto.verify(
    "RSA-SHA256",
    Buffer.from(`${parts[0]}.${parts[1]}`),
    publicKey,
    Buffer.from(parts[2], "base64url"),
  );
  if (!valid) throw new Error("The ID token signature is invalid.");
  if (payload.iss !== ISSUER) throw new Error("The ID token issuer is not trusted.");
  if (payload.aud !== CLIENT_ID) throw new Error("The ID token audience is not this app.");
  if (Number(payload.exp || 0) * 1000 <= Date.now()) throw new Error("The ID token has expired.");
  if (expectedNonce && payload.nonce !== expectedNonce) throw new Error("The ID token nonce does not match.");
  return {
    payload,
    checks: [
      "Signature verified against JWKS",
      `Issuer is ${ISSUER}`,
      `Audience is ${CLIENT_ID}`,
      "Token is not expired",
      expectedNonce ? "Nonce matches the authorization request" : "Nonce not required for this flow",
    ],
  };
}

async function fetchUserinfo(accessToken) {
  const response = await fetch(`${API_INTERNAL_BASE}/v1/oauth/userinfo`, {
    headers: { authorization: `Bearer ${accessToken}` },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(errorMessage(body, "The userinfo request was rejected."));
  }
  return body;
}

function purgeExpired() {
  const now = Date.now();
  for (const [key, value] of pending) if (now - value.createdAt > STATE_TTL_MS) pending.delete(key);
  for (const [key, value] of pendingMfa) if (now - value.createdAt > STATE_TTL_MS) pendingMfa.delete(key);
  for (const [key, value] of sessions) if (now - value.createdAt > SESSION_TTL_MS) sessions.delete(key);
}

async function completeLogin(tokens, expectedNonce) {
  if (!tokens.access_token || !tokens.id_token) {
    throw new Error("Basacore did not return the expected tokens.");
  }
  const verified = await verifyIdToken(tokens.id_token, expectedNonce);
  const userinfo = await fetchUserinfo(tokens.access_token);
  const sid = randomId();
  sessions.set(sid, {
    tokens,
    claims: verified.payload,
    checks: verified.checks,
    userinfo,
    createdAt: Date.now(),
  });
  return sid;
}

function currentSession(request) {
  const cookies = parseCookies(request.headers.cookie);
  const sid = cookies[SESSION_COOKIE];
  if (!sid) return null;
  const session = sessions.get(sid);
  if (!session) return null;
  if (Date.now() - session.createdAt > SESSION_TTL_MS) {
    sessions.delete(sid);
    return null;
  }
  return { sid, ...session };
}

function page(title, body, options = {}) {
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>${escapeHtml(title)}</title>
<style>
  :root { color-scheme: light; --ink:#12161f; --muted:#5b6474; --line:#dfe3ea; --brand:#1f5eff; --panel:#ffffff; --bg:#f4f6fb; --ok:#0a7b47; }
  * { box-sizing: border-box; }
  body { margin:0; font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif; background:var(--bg); color:var(--ink); }
  header.top { background:#0f1420; color:#fff; padding:18px 28px; display:flex; justify-content:space-between; align-items:center; gap:16px; flex-wrap:wrap; }
  header.top .brand { font-weight:700; letter-spacing:-0.02em; font-size:1.1rem; }
  header.top .sub { color:#9aa6bd; font-size:.82rem; }
  main { max-width:1080px; margin:0 auto; padding:32px 24px 64px; display:grid; gap:22px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(300px, 1fr)); gap:22px; align-items:start; }
  .card { background:var(--panel); border:1px solid var(--line); border-radius:16px; padding:24px; box-shadow:0 1px 2px rgba(16,24,40,.04); }
  .card h2 { margin:0 0 8px; font-size:1.15rem; letter-spacing:-0.02em; }
  .card p.lead { margin:0 0 18px; color:var(--muted); line-height:1.55; }
  .eyebrow { text-transform:uppercase; letter-spacing:.14em; font-size:.68rem; font-weight:700; color:var(--brand); margin:0 0 8px; }
  label { display:block; font-size:.8rem; font-weight:600; margin:14px 0 6px; }
  input { width:100%; padding:11px 12px; border:1px solid var(--line); border-radius:10px; font:inherit; background:#fff; }
  .button { display:inline-flex; align-items:center; justify-content:center; gap:8px; padding:11px 16px; border-radius:10px; border:1px solid transparent; background:var(--brand); color:#fff; font:inherit; font-weight:600; cursor:pointer; text-decoration:none; }
  .button.secondary { background:#fff; color:var(--ink); border-color:var(--line); }
  .button.full { width:100%; }
  .row { display:flex; gap:10px; flex-wrap:wrap; align-items:center; }
  .badge { display:inline-block; padding:4px 10px; border-radius:999px; background:#eef3ff; color:#1f3f99; font-size:.75rem; font-weight:700; }
  .badge.ok { background:#e7f7ee; color:var(--ok); }
  .muted { color:var(--muted); }
  .small { font-size:.82rem; line-height:1.55; }
  .list { margin:0; padding-left:18px; line-height:1.7; }
  .chips { display:flex; flex-wrap:wrap; gap:8px; margin:8px 0 0; padding:0; list-style:none; }
  .chips li { background:#f4f6fb; border:1px solid var(--line); border-radius:999px; padding:4px 10px; font-size:.75rem; }
  pre { background:#0f1420; color:#d8e2ff; padding:16px; border-radius:12px; overflow:auto; font-size:.74rem; line-height:1.5; }
  code { font-size:.85em; }
  details summary { cursor:pointer; font-weight:600; margin-bottom:8px; }
  .alert { border:1px solid #f2c7c7; background:#fff5f5; color:#8a1f1f; padding:12px 14px; border-radius:10px; margin-bottom:16px; }
  .notice { border:1px solid #bfe5cf; background:#f1fbf5; color:#0a5c37; padding:12px 14px; border-radius:10px; }
  dl.meta { display:grid; grid-template-columns:minmax(120px, 180px) 1fr; gap:8px 16px; margin:0; font-size:.86rem; }
  dl.meta dt { color:var(--muted); } dl.meta dd { margin:0; overflow-wrap:anywhere; }
  footer { border-top:1px solid var(--line); padding-top:18px; color:var(--muted); font-size:.8rem; }
</style>
</head>
<body>
<header class="top">
  <div>
    <div class="brand">Atlas CRM</div>
    <div class="sub">External customer app · authenticated by Basacore</div>
  </div>
  <div class="sub">tenant hint: <code>${escapeHtml(TENANT_HINT)}</code></div>
</header>
<main>
${body}
<footer>
  Demo client <code>${escapeHtml(CLIENT_ID)}</code> · redirect <code>${escapeHtml(REDIRECT_URI)}</code>.
  This app never sees a password for hosted login and never trusts an unverified token.
</footer>
</main>
</body>
</html>`;
}

function errorPage(message, status = 400) {
  const body = `
  <section class="card">
    <p class="eyebrow">Sign-in failed</p>
    <h2>Basacore did not complete the sign-in</h2>
    <div class="alert">${escapeHtml(message)}</div>
    <a class="button" href="/">Try again</a>
  </section>`;
  return { status, html: page("Sign-in failed", body) };
}

function signedOutPage() {
  const body = `
  <section class="card">
    <p class="eyebrow">Session ended</p>
    <h2>You are signed out of Atlas CRM and Basacore</h2>
    <p class="lead">The local tokens were revoked and the Basacore session cookie was cleared. The next sign-in starts a fresh authorization flow.</p>
    <a class="button" href="/">Sign in again</a>
  </section>`;
  return page("Signed out", body);
}

function homePage(session, notice) {
  if (!session) {
    const body = `
    ${notice ? `<div class="notice">${escapeHtml(notice)}</div>` : ""}
    <div class="grid">
      <section class="card">
        <p class="eyebrow">Hosted login (recommended)</p>
        <h2>Send the user to Basacore</h2>
        <p class="lead">The browser is redirected to Basacore with this application's client id, a PKCE challenge, a nonce and the tenant hint. After sign-in, Basacore returns an authorization code to this app.</p>
        <dl class="meta">
          <dt>Application</dt><dd>Atlas CRM</dd>
          <dt>Tenant hint</dt><dd><code>${escapeHtml(TENANT_HINT)}</code></dd>
          <dt>Scopes</dt><dd><code>${escapeHtml(SCOPE)}</code></dd>
          <dt>PKCE</dt><dd>S256 (verifier kept server-side)</dd>
        </dl>
        <p style="margin-top:20px"><a class="button full" href="/auth/login">Sign in with Basacore</a></p>
        <p class="small muted">Demo user <code>${escapeHtml(DEMO_EMAIL)}</code> · password from the seed dataset.</p>
      </section>

      <section class="card">
        <p class="eyebrow">Embedded login (server-side)</p>
        <h2>Collect credentials in this app</h2>
        <p class="lead">The email and password are posted to this server, which calls Basacore's embedded endpoint with the confidential client secret. The secret and the resulting tokens never touch browser JavaScript.</p>
        <form method="post" action="/auth/embedded">
          <label for="email">Work email</label>
          <input id="email" name="email" type="email" value="${escapeHtml(DEMO_EMAIL)}" required autocomplete="username" />
          <label for="password">Password</label>
          <input id="password" name="password" type="password" required autocomplete="current-password" />
          <label for="tenant">Tenant hint</label>
          <input id="tenant" name="tenant" value="${escapeHtml(TENANT_HINT)}" required />
          <p style="margin-top:18px"><button class="button full secondary" type="submit">Sign in inside Atlas CRM</button></p>
        </form>
      </section>
    </div>`;
    return page("Atlas CRM · Sign in", body);
  }

  const info = session.userinfo;
  const claims = session.claims;
  const roles = (info.roles || []).map((role) => `<li>${escapeHtml(role)}</li>`).join("");
  const permissions = (info.permissions || [])
    .map((permission) => `<li>${escapeHtml(permission)}</li>`)
    .join("");
  const checks = (session.checks || []).map((check) => `<li>${escapeHtml(check)}</li>`).join("");

  const body = `
  ${notice ? `<div class="notice">${escapeHtml(notice)}</div>` : ""}
  <section class="card">
    <div class="row" style="justify-content:space-between">
      <div>
        <p class="eyebrow">Authenticated session</p>
        <h2>${escapeHtml(info.name || claims.name)}</h2>
        <p class="lead" style="margin-bottom:0">${escapeHtml(info.email || claims.email)}</p>
      </div>
      <span class="badge ok">ID token verified</span>
    </div>
    <div class="grid" style="margin-top:20px">
      <div>
        <dl class="meta">
          <dt>Tenant</dt><dd>${escapeHtml(info.tenant_slug)} <span class="muted">(${escapeHtml(info.tenant_id)})</span></dd>
          <dt>Application</dt><dd>${escapeHtml(info.app_id)}</dd>
          <dt>Client</dt><dd>${escapeHtml(info.client_id)}</dd>
          <dt>Subject</dt><dd>${escapeHtml(info.sub)}</dd>
          <dt>Session</dt><dd>${escapeHtml(info.sid)}</dd>
        </dl>
      </div>
      <div>
        <p class="small" style="margin:0 0 6px"><strong>Roles</strong></p>
        <ul class="chips">${roles || "<li>none</li>"}</ul>
        <p class="small" style="margin:14px 0 6px"><strong>Permissions from Basacore</strong></p>
        <ul class="chips">${permissions || "<li>none</li>"}</ul>
      </div>
    </div>
    <div class="row" style="margin-top:22px">
      <form method="post" action="/auth/refresh"><button class="button secondary" type="submit">Rotate tokens</button></form>
      <form method="post" action="/auth/logout"><button class="button" type="submit">Sign out everywhere</button></form>
    </div>
  </section>

  <div class="grid">
    <section class="card">
      <h2>What the ID token proved</h2>
      <ul class="list">${checks}</ul>
      <p class="small muted" style="margin-bottom:0">Roles and permissions above come from the userinfo endpoint, which re-checks the server-side session on every call.</p>
    </section>
    <section class="card">
      <h2>Token material</h2>
      <details>
        <summary>Access token (Bearer)</summary>
        <pre>${escapeHtml(session.tokens.access_token)}</pre>
      </details>
      <details>
        <summary>ID token</summary>
        <pre>${escapeHtml(session.tokens.id_token)}</pre>
      </details>
      <details>
        <summary>Refresh token (rotated on every use)</summary>
        <p class="small">Stored only in this server's memory; the browser holds an opaque session cookie. Prefix: <code>${escapeHtml(String(session.tokens.refresh_token).slice(0, 10))}…</code></p>
      </details>
    </section>
  </div>`;
  return page("Atlas CRM · Account", body);
}

function mfaPage(pendingId) {
  const body = `
  <section class="card" style="max-width:520px;margin:0 auto">
    <p class="eyebrow">Second factor</p>
    <h2>Enter your authenticator code</h2>
    <p class="lead">Basacore requires MFA for this account. The challenge token stays on the server, next to the client credentials.</p>
    <form method="post" action="/auth/embedded/mfa">
      <input type="hidden" name="pending" value="${escapeHtml(pendingId)}" />
      <label for="code">Authenticator code</label>
      <input id="code" name="code" inputmode="numeric" autocomplete="one-time-code" required autofocus />
      <p style="margin-top:18px"><button class="button full" type="submit">Verify and continue</button></p>
    </form>
  </section>`;
  return page("Atlas CRM · Second factor", body);
}

function send(response, status, html, headers = {}) {
  response.writeHead(status, {
    "content-type": "text/html; charset=utf-8",
    "cache-control": "no-store",
    "referrer-policy": "no-referrer",
    "x-frame-options": "DENY",
    "x-content-type-options": "nosniff",
    "content-security-policy":
      "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
    ...headers,
  });
  response.end(html);
}

function redirect(response, location, headers = {}) {
  response.writeHead(303, { location, "cache-control": "no-store", ...headers });
  response.end();
}

async function handle(request, response) {
  purgeExpired();
  const url = new URL(request.url, PUBLIC_BASE);
  const route = url.pathname;
  const method = request.method || "GET";

  if (route === "/healthz") {
    response.writeHead(200, { "content-type": "text/plain" });
    response.end("ok");
    return;
  }

  if (method === "GET" && route === "/") {
    if (url.searchParams.get("state") === "signed_out") {
      send(response, 200, signedOutPage());
      return;
    }
    const session = currentSession(request);
    const notice = url.searchParams.get("error");
    send(response, 200, homePage(session, notice));
    return;
  }

  if (method === "GET" && route === "/auth/login") {
    const verifier = pkce();
    const state = randomId();
    const nonce = randomId();
    pending.set(state, { verifier: verifier.verifier, nonce, createdAt: Date.now() });
    const params = new URLSearchParams({
      response_type: "code",
      client_id: CLIENT_ID,
      redirect_uri: REDIRECT_URI,
      scope: SCOPE,
      state,
      nonce,
      code_challenge: verifier.challenge,
      code_challenge_method: "S256",
      tenant: TENANT_HINT,
    });
    redirect(response, `${API_BASE}/v1/oauth/authorize?${params.toString()}`);
    return;
  }

  if (method === "GET" && route === "/auth/callback") {
    if (url.searchParams.get("error")) {
      const description =
        url.searchParams.get("error_description") || url.searchParams.get("error") || "Unknown error";
      const pageResult = errorPage(description);
      send(response, pageResult.status, pageResult.html);
      return;
    }
    const state = url.searchParams.get("state") || "";
    const record = pending.get(state);
    pending.delete(state);
    if (!record) {
      const pageResult = errorPage("The authorization state is unknown or has expired. Start again.");
      send(response, pageResult.status, pageResult.html);
      return;
    }
    const code = url.searchParams.get("code");
    if (!code) {
      const pageResult = errorPage("Basacore returned no authorization code.");
      send(response, pageResult.status, pageResult.html);
      return;
    }
    const exchange = await postForm(
      `${API_INTERNAL_BASE}/v1/oauth/token`,
      {
        grant_type: "authorization_code",
        client_id: CLIENT_ID,
        client_secret: CLIENT_SECRET,
        code,
        redirect_uri: REDIRECT_URI,
        code_verifier: record.verifier,
      },
      { authorization: `Basic ${Buffer.from(`${CLIENT_ID}:${CLIENT_SECRET}`).toString("base64")}` },
    );
    if (!exchange.ok) {
      const pageResult = errorPage(
        errorMessage(exchange.body, "The token exchange failed."),
      );
      send(response, pageResult.status, pageResult.html);
      return;
    }
    const sid = await completeLogin(exchange.body, record.nonce);
    redirect(response, "/", { "set-cookie": sessionCookie(sid, Math.floor(SESSION_TTL_MS / 1000)) });
    return;
  }

  if (method === "POST" && route === "/auth/embedded") {
    const form = await readForm(request);
    const result = await postJson(`${API_INTERNAL_BASE}/v1/oauth/embedded/login`, {
      client_id: CLIENT_ID,
      client_secret: CLIENT_SECRET,
      tenant: form.get("tenant") || TENANT_HINT,
      email: form.get("email") || "",
      password: form.get("password") || "",
      scope: SCOPE,
    });
    if (!result.ok) {
      redirect(
        response,
        `/?error=${encodeURIComponent(
          errorMessage(result.body, "Embedded sign-in failed."),
        )}`,
      );
      return;
    }
    if (result.body.status === "mfa_required") {
      const pendingId = randomId();
      pendingMfa.set(pendingId, {
        challenge: result.body.challenge_token,
        tenant: form.get("tenant") || TENANT_HINT,
        createdAt: Date.now(),
      });
      send(response, 200, mfaPage(pendingId));
      return;
    }
    const sid = await completeLogin(result.body, null);
    redirect(response, "/", { "set-cookie": sessionCookie(sid, Math.floor(SESSION_TTL_MS / 1000)) });
    return;
  }

  if (method === "POST" && route === "/auth/embedded/mfa") {
    const form = await readForm(request);
    const pendingId = form.get("pending") || "";
    const record = pendingMfa.get(pendingId);
    pendingMfa.delete(pendingId);
    if (!record) {
      redirect(response, `/?error=${encodeURIComponent("The MFA challenge has expired.")}`);
      return;
    }
    const result = await postJson(`${API_INTERNAL_BASE}/v1/oauth/embedded/mfa`, {
      client_id: CLIENT_ID,
      client_secret: CLIENT_SECRET,
      tenant: record.tenant,
      challenge_token: record.challenge,
      code: form.get("code") || "",
      scope: SCOPE,
    });
    if (!result.ok) {
      redirect(
        response,
        `/?error=${encodeURIComponent(
          errorMessage(result.body, "MFA verification failed."),
        )}`,
      );
      return;
    }
    const sid = await completeLogin(result.body, null);
    redirect(response, "/", { "set-cookie": sessionCookie(sid, Math.floor(SESSION_TTL_MS / 1000)) });
    return;
  }

  if (method === "POST" && route === "/auth/refresh") {
    const session = currentSession(request);
    if (!session) {
      redirect(response, `/?error=${encodeURIComponent("Your session has expired.")}`);
      return;
    }
    const result = await postForm(`${API_INTERNAL_BASE}/v1/oauth/token`, {
      grant_type: "refresh_token",
      client_id: CLIENT_ID,
      client_secret: CLIENT_SECRET,
      refresh_token: session.tokens.refresh_token,
      scope: SCOPE,
    });
    if (!result.ok) {
      sessions.delete(session.sid);
      redirect(
        response,
        `/?error=${encodeURIComponent(
          errorMessage(result.body, "Token rotation failed."),
        )}`,
        { "set-cookie": sessionCookie("", 0) },
      );
      return;
    }
    const verified = await verifyIdToken(result.body.id_token, session.claims.nonce);
    const userinfo = await fetchUserinfo(result.body.access_token);
    sessions.set(session.sid, {
      tokens: result.body,
      claims: verified.payload,
      checks: verified.checks,
      userinfo,
      createdAt: Date.now(),
    });
    redirect(response, "/");
    return;
  }

  if (method === "POST" && route === "/auth/logout") {
    const session = currentSession(request);
    if (session) {
      await postForm(
        `${API_INTERNAL_BASE}/v1/oauth/revoke`,
        { token: session.tokens.refresh_token, client_id: CLIENT_ID },
        { authorization: `Basic ${Buffer.from(`${CLIENT_ID}:${CLIENT_SECRET}`).toString("base64")}` },
      ).catch(() => undefined);
      sessions.delete(session.sid);
    }
    const logout = new URLSearchParams({
      client_id: CLIENT_ID,
      post_logout_redirect_uri: `${PUBLIC_BASE}/`,
      state: "signed_out",
    });
    redirect(response, `${API_BASE}/v1/oauth/logout?${logout.toString()}`, {
      "set-cookie": sessionCookie("", 0),
    });
    return;
  }

  response.writeHead(404, { "content-type": "text/plain" });
  response.end("Not found");
}

const server = http.createServer((request, response) => {
  handle(request, response).catch((error) => {
    console.error("[demo-app]", error);
    const pageResult = errorPage(
      error && error.message ? error.message : "The demo application hit an unexpected error.",
      500,
    );
    send(response, pageResult.status, pageResult.html);
  });
});

server.listen(PORT, () => {
  console.log(`[demo-app] Atlas CRM listening on http://localhost:${PORT}`);
  console.log(`[demo-app] Basacore API ${API_BASE} · tenant hint ${TENANT_HINT}`);
});


