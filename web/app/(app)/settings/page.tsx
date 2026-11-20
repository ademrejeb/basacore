import Link from "next/link";
import { redirect } from "next/navigation";
import {
  KeyRound,
  Laptop,
  LockKeyhole,
  LogOut,
  Mail,
  ShieldCheck,
  Smartphone,
  UserRound,
} from "lucide-react";

import { Badge, FormField, Input } from "@adem.rejeb/ui";

import {
  changePasswordAction,
  disableMfaAction,
  revokeAllSessionsAction,
  revokeSessionAction,
} from "@/app/actions";
import { ActionDialog } from "@/components/action-dialog";
import { MfaSetup } from "@/components/mfa-setup";
import { EmptyState } from "@/components/pagination";
import { QuickActionForm } from "@/components/quick-action-form";
import { StatusBadge } from "@/components/status-badge";
import { apiFetch, getSession } from "@/lib/api";
import { dateTime, relativeTime, scopeLabel, titleCase } from "@/lib/format";
import type { AuthEvent, AuthSession } from "@/lib/types";

export const metadata = { title: "Settings" };

function eventLabel(value: string): string {
  const labels: Record<string, string> = {
    login: "Signed in",
    login_failed: "Sign-in failed",
    logout: "Signed out",
    session_created: "Session started",
    session_revoked: "Session signed out",
    password_changed: "Password changed",
    password_reset: "Password reset",
    mfa_enabled: "Authenticator enabled",
    mfa_disabled: "Authenticator disabled",
    mfa_challenge: "Authenticator check",
    mfa_verified: "Authenticator verified",
    mfa_failed: "Authenticator check failed",
  };
  return labels[value] ?? titleCase(value);
}

export default async function SettingsPage() {
  const session = await getSession();
  if (!session) redirect("/login");

  const [sessions, events] = await Promise.all([
    apiFetch<AuthSession[]>("/v1/auth/sessions"),
    apiFetch<AuthEvent[]>("/v1/auth/events?limit=25"),
  ]);

  const activeSessions = sessions.filter((item) => !item.revoked_at);
  const otherSessions = activeSessions.filter((item) => !item.current);

  return (
    <div className="page-stack">
      <div className="page-heading">
        <div>
          <span className="eyebrow">Your account</span>
          <h1>Settings</h1>
          <p>
            Manage your profile, active sessions and multi-factor authentication. Every
            change is recorded in your sign-in history.
          </p>
        </div>
        <div className="page-heading__actions">
          <Badge variant={session.user.mfa_enabled ? "success" : "warning"} size="sm" dot>
            {session.user.mfa_enabled ? "MFA enabled" : "MFA not enabled"}
          </Badge>
          <StatusBadge status={session.user.status} />
        </div>
      </div>

      <section className="detail-hero">
        <span className="detail-hero__mark">
          {session.user.full_name.slice(0, 2).toUpperCase()}
        </span>
        <div style={{ flex: 1 }}>
          <h1>{session.user.full_name}</h1>
          <p>
            {session.user.job_title ?? "No job title"} ·{" "}
            {session.user.department ?? "No department"} · {titleCase(session.user.user_type)} account
          </p>
          <div className="row small muted" style={{ marginTop: 10, gap: 14 }}>
            <span className="mono">{session.user.email}</span>
            <span>Last sign-in {session.user.last_login_at ? relativeTime(session.user.last_login_at) : "never"}</span>
          </div>
        </div>
        <div className="stack" style={{ minWidth: 160 }}>
          <div className="kv-label">Scopes</div>
          <div className="pill-list">
            {session.scopes.map((scope) => (
              <span className="tag-mono" key={`${scope.scope_type}-${scope.scope_id ?? "global"}`}>
                {scopeLabel(scope.scope_type)} · {scope.role_keys.join(", ") || "member"}
              </span>
            ))}
            {session.scopes.length === 0 ? <span className="small muted">No scopes</span> : null}
          </div>
        </div>
      </section>

      <section className="detail-grid">
        <div className="section-card">
          <div className="section-card__head">
            <div>
              <h2>Profile</h2>
              <p>Your profile details are managed by a platform administrator.</p>
            </div>
            <UserRound size={17} />
          </div>
          <div className="section-card__body">
            <div className="kv-grid">
              <div>
                <div className="kv-label">Full name</div>
                <div className="kv-value">{session.user.full_name}</div>
              </div>
              <div>
                <div className="kv-label">Email</div>
                <div className="kv-value mono">{session.user.email}</div>
              </div>
              <div>
                <div className="kv-label">Account type</div>
                <div className="kv-value">{titleCase(session.user.user_type)}</div>
              </div>
              <div>
                <div className="kv-label">User id</div>
                <div className="kv-value mono">{session.user.id}</div>
              </div>
              <div>
                <div className="kv-label">Job title</div>
                <div className="kv-value">{session.user.job_title ?? "—"}</div>
              </div>
              <div>
                <div className="kv-label">Department</div>
                <div className="kv-value">{session.user.department ?? "—"}</div>
              </div>
            </div>
          </div>
        </div>

        <div className="section-card">
          <div className="section-card__head">
            <div>
              <h2>Password</h2>
              <p>Passwords are stored securely and never in clear text.</p>
            </div>
            <LockKeyhole size={17} />
          </div>
          <div className="section-card__body stack">
            <div className="alert-row">
              <div className="alert-row__meta">
                <strong>Change password</strong>
                <span>Minimum 12 characters. Other sessions are signed out.</span>
              </div>
              <ActionDialog
                label="Change password"
                triggerVariant="outline"
                title="Change your password"
                description="Your current password is required to prevent account takeover."
                action={changePasswordAction}
                submitLabel="Change password"
              >
                <FormField label="Current password" required>
                  <Input name="current_password" type="password" required autoComplete="current-password" />
                </FormField>
                <FormField label="New password" required description="Minimum 12 characters.">
                  <Input
                    name="new_password"
                    type="password"
                    minLength={12}
                    required
                    autoComplete="new-password"
                  />
                </FormField>
              </ActionDialog>
            </div>
            <div className="row small muted">
              <KeyRound size={14} /> Password resets are issued through the reset flow and expire
              after a short window.
            </div>
          </div>
        </div>
      </section>

      <section className="section-card">
        <div className="section-card__head">
          <div>
            <h2>Multi-factor authentication</h2>
            <p>TOTP codes and single-use recovery codes protect your account.</p>
          </div>
          <ShieldCheck size={17} />
        </div>
        <div className="section-card__body stack">
          <MfaSetup enabled={session.user.mfa_enabled} />
          {session.user.mfa_enabled ? (
            <div className="alert-row">
              <div className="alert-row__meta">
                <strong>Disable MFA</strong>
                <span>Requires your current password. Existing sessions remain active.</span>
              </div>
              <ActionDialog
                label="Disable MFA"
                triggerVariant="ghost"
                title="Disable multi-factor authentication"
                description="Your account will rely on password authentication alone."
                action={disableMfaAction}
                submitLabel="Disable MFA"
                destructive
              >
                <FormField label="Current password" required>
                  <Input name="current_password" type="password" required autoComplete="current-password" />
                </FormField>
              </ActionDialog>
            </div>
          ) : null}
          <div className="row small muted">
            <Smartphone size={14} /> Recovery codes are single-use and hashed at rest.
          </div>
        </div>
      </section>

      <div className="section-card">
        <div className="section-card__head">
          <div>
            <h2>Active sessions</h2>
            <p>
              {activeSessions.length} open · {otherSessions.length} other device
              {otherSessions.length === 1 ? "" : "s"}.
            </p>
          </div>
          <div className="page-heading__actions">
            {otherSessions.length > 0 ? (
              <ActionDialog
                label="Revoke all other sessions"
                triggerVariant="outline"
                icon={<LogOut size={15} />}
                title="Sign out other devices"
                description="Every other session is signed out immediately."
                action={revokeAllSessionsAction}
                submitLabel="Revoke sessions"
                destructive
              >
                <p className="small muted" style={{ margin: 0 }}>
                  This cannot be undone. You can sign in again from any device.
                </p>
              </ActionDialog>
            ) : null}
            <Link className="ui-button ui-button--outline" href="/audit">
              Activity log
            </Link>
          </div>
        </div>

        {activeSessions.length === 0 ? (
          <EmptyState
            title="No active sessions"
            description="Your current session will appear here as soon as the session list refreshes."
          />
        ) : (
          <div className="data-table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Device</th>
                  <th>IP address</th>
                  <th>MFA</th>
                  <th>Created</th>
                  <th>Last seen</th>
                  <th>Expires</th>
                  <th className="align-end">Actions</th>
                </tr>
              </thead>
              <tbody>
                {activeSessions.map((item) => (
                  <tr key={item.id}>
                    <td>
                      <span className="cell-primary">
                        <Laptop size={14} style={{ verticalAlign: "-2px" }} />{" "}
                        {item.current ? "This device" : deviceName(item.user_agent)}
                      </span>
                    </td>
                    <td className="mono">{item.ip_address ?? "—"}</td>
                    <td>
                      {item.mfa_verified ? (
                        <Badge variant="success" size="sm">
                          Verified
                        </Badge>
                      ) : (
                        <span className="small muted">Password only</span>
                      )}
                    </td>
                    <td className="muted">{dateTime(item.created_at)}</td>
                    <td className="muted">{relativeTime(item.last_seen_at)}</td>
                    <td className="muted">{dateTime(item.expires_at)}</td>
                    <td className="align-end">
                      {item.current ? (
                        <Badge variant="info" size="sm">
                          Current
                        </Badge>
                      ) : (
                        <QuickActionForm
                          action={revokeSessionAction}
                          fields={{ session_id: item.id }}
                          label="Revoke"
                          variant="ghost"
                        />
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <section className="section-card">
        <div className="section-card__head">
          <div>
            <h2>Sign-in history</h2>
            <p>Sign-ins, MFA challenges, password changes and revocations.</p>
          </div>
          <Mail size={17} />
        </div>
        <div className="section-card__body">
          {events.length === 0 ? (
            <p className="small muted" style={{ margin: 0 }}>
              No sign-in events recorded yet.
            </p>
          ) : (
            <div className="timeline">
              {events.map((event) => (
                <div className="timeline__item" key={event.id}>
                  <span className="timeline__dot" />
                  <div className="timeline__body">
                    <strong className="audit-action">{eventLabel(event.event_type)}</strong>
                    <span>
                      {titleCase(event.outcome)} · {event.ip_address ?? "no address recorded"} ·{" "}
                      {relativeTime(event.created_at)}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}

function deviceName(userAgent: string | null): string {
  if (!userAgent) return "Unknown device";
  if (/firefox/i.test(userAgent)) return "Firefox";
  if (/edg/i.test(userAgent)) return "Edge";
  if (/chrome|chromium/i.test(userAgent)) return "Chrome";
  if (/safari/i.test(userAgent)) return "Safari";
  if (/curl|httpx|python|node/i.test(userAgent)) return "API client";
  return "Browser session";
}
