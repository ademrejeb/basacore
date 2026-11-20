import Link from "next/link";
import { notFound } from "next/navigation";
import {
  Archive,
  BadgeCheck,
  Globe,
  Pencil,
  Plus,
  RefreshCw,
  ShieldCheck,
  Trash2,
  UserPlus,
  Zap,
} from "lucide-react";

import {
  Badge,
  Checkbox,
  FormField,
  Input,
  Select,
  Textarea,
} from "@adem.rejeb/ui";

import {
  addTenantDomainAction,
  changeSubscriptionAction,
  deleteTenantDomainAction,
  inviteTenantUserAction,
  publishTenantConfigAction,
  removeTenantUserAction,
  tenantLifecycleAction,
  updateTenantAction,
  updateTenantUserAction,
  verifyTenantDomainAction,
} from "@/app/actions";
import { ActionDialog } from "@/components/action-dialog";
import { EmptyState } from "@/components/pagination";
import { QuickActionForm } from "@/components/quick-action-form";
import { StatusBadge } from "@/components/status-badge";
import { apiFetch, ApiError } from "@/lib/api";
import {
  date,
  dateTime,
  metricLabel,
  money,
  number,
  percent,
  relativeTime,
  titleCase,
  actionLabel,
} from "@/lib/format";
import type { Plan, TenantDetail } from "@/lib/types";

export const metadata = { title: "Workspace" };

const TENANT_ROLES = [
  { key: "tenant_owner", label: "Owner" },
  { key: "tenant_admin", label: "Administrator" },
  { key: "tenant_manager", label: "Manager" },
  { key: "tenant_member", label: "Member" },
];

type Params = Promise<{ id: string }>;

export default async function TenantDetailPage({ params }: { params: Params }) {
  const { id } = await params;

  let tenant: TenantDetail;
  let plans: Plan[];
  try {
    [tenant, plans] = await Promise.all([
      apiFetch<TenantDetail>(`/v1/tenants/${id}`),
      apiFetch<Plan[]>("/v1/billing/plans"),
    ]);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) notFound();
    throw error;
  }

  const canSuspend = !["suspended", "archived", "deleted"].includes(tenant.status);
  const canReactivate = ["suspended", "archived"].includes(tenant.status);
  const canArchive = !["archived", "deleted"].includes(tenant.status);

  return (
    <div className="page-stack">
      <div className="detail-hero">
        <span className="detail-hero__mark">{tenant.name.slice(0, 2).toUpperCase()}</span>
        <div style={{ flex: 1 }}>
          <div className="row" style={{ gap: 10 }}>
            <h1>{tenant.name}</h1>
            <StatusBadge status={tenant.status} />
            <Badge variant="outline" size="sm" icon={<ShieldCheck size={12} />}>
              Workspace
            </Badge>
          </div>
          <div className="row small muted" style={{ marginTop: 8, gap: 14 }}>
            <span className="mono">{tenant.slug}</span>
            <span>
              Application:{" "}
              <Link href={`/applications/${tenant.application_id}`}>
                {tenant.application_name ?? "Open"}
              </Link>
            </span>
            <span>
              Organization:{" "}
              <Link href={`/organizations/${tenant.organization_id}`}>Open organization</Link>
            </span>
            <span>Created {date(tenant.created_at)}</span>
          </div>
        </div>
        <div className="page-heading__actions">
          <Link className="ui-button ui-button--outline" href="/tenants">
            All workspaces
          </Link>
          <ActionDialog
            label="Edit workspace"
            triggerVariant="secondary"
            icon={<Pencil size={15} />}
            title="Edit workspace"
            description="Name and limit changes are recorded in the activity log."
            action={updateTenantAction}
            submitLabel="Save changes"
          >
            <input type="hidden" name="id" value={tenant.id} />
            <FormField label="Name" required>
              <Input name="name" defaultValue={tenant.name} required />
            </FormField>
            <FormField label="Limits" description="JSON object of integer limits.">
              <Textarea name="quotas" rows={4} defaultValue={JSON.stringify(tenant.quotas, null, 2)} />
            </FormField>
          </ActionDialog>
        </div>
      </div>

      <section className="metric-grid">
        <div className="section-card">
          <div className="section-card__body">
            <div className="kv-label">Plan</div>
            <div className="stat-inline">
              <strong>{tenant.subscription?.plan_name ?? "None"}</strong>
              <span>{tenant.subscription ? money(tenant.subscription.mrr_cents) + "/mo" : ""}</span>
            </div>
          </div>
        </div>
        <div className="section-card">
          <div className="section-card__body">
            <div className="kv-label">Seats</div>
            <div className="stat-inline">
              <strong>{number(tenant.counts.users)}</strong>
              <span>of {number(tenant.quotas.seats ?? 0)}</span>
            </div>
          </div>
        </div>
        <div className="section-card">
          <div className="section-card__body">
            <div className="kv-label">Domains</div>
            <div className="stat-inline">
              <strong>{number(tenant.domains.length)}</strong>
              <span>{tenant.domains.filter((domain) => domain.status === "verified").length} verified</span>
            </div>
          </div>
        </div>
        <div className="section-card">
          <div className="section-card__body">
            <div className="kv-label">Trial ends</div>
            <div className="stat-inline">
              <strong>{tenant.trial_ends_at ? date(tenant.trial_ends_at) : "—"}</strong>
              <span>{tenant.subscription?.status ? titleCase(tenant.subscription.status) : ""}</span>
            </div>
          </div>
        </div>
      </section>

      <section className="section-card">
        <div className="section-card__head">
          <div>
            <h2>Status controls</h2>
            <p>Every change is recorded in this workspace&apos;s activity log.</p>
          </div>
          <div className="row" style={{ gap: 8 }}>
            {canSuspend ? (
              <ActionDialog
                label="Suspend"
                triggerVariant="outline"
                icon={<Zap size={15} />}
                title="Suspend workspace"
                description="Suspending blocks access but keeps all data and billing records."
                action={tenantLifecycleAction}
                submitLabel="Suspend workspace"
              >
                <input type="hidden" name="id" value={tenant.id} />
                <input type="hidden" name="intent" value="suspend" />
                <FormField label="Reason">
                  <Input name="reason" placeholder="Payment review" />
                </FormField>
              </ActionDialog>
            ) : null}
            {canReactivate ? (
              <ActionDialog
                label="Reactivate"
                triggerVariant="primary"
                icon={<RefreshCw size={15} />}
                title="Reactivate workspace"
                description="Reactivation restores access. Past-due subscriptions must be brought up to date first."
                action={tenantLifecycleAction}
                submitLabel="Reactivate workspace"
              >
                <input type="hidden" name="id" value={tenant.id} />
                <input type="hidden" name="intent" value="reactivate" />
                <FormField label="Reason">
                  <Input name="reason" placeholder="Review complete" />
                </FormField>
              </ActionDialog>
            ) : null}
            {canArchive ? (
              <ActionDialog
                label="Archive"
                triggerVariant="ghost"
                icon={<Archive size={15} />}
                title="Archive workspace"
                description="Archiving can be undone. Data stays separate and is kept."
                action={tenantLifecycleAction}
                submitLabel="Archive workspace"
              >
                <input type="hidden" name="id" value={tenant.id} />
                <input type="hidden" name="intent" value="archive" />
                <FormField label="Reason">
                  <Input name="reason" placeholder="Customer churned" />
                </FormField>
              </ActionDialog>
            ) : null}
            <ActionDialog
              label="Delete"
              triggerVariant="ghost"
              icon={<Trash2 size={15} />}
              title="Permanently delete workspace"
              description="The workspace is deleted and blocked from access. Type its name exactly to confirm."
              action={tenantLifecycleAction}
              submitLabel="Delete workspace"
              destructive
            >
              <input type="hidden" name="id" value={tenant.id} />
              <input type="hidden" name="intent" value="delete" />
              <FormField label={`Type “${tenant.name}” to confirm`} required>
                <Input name="confirm_name" required placeholder={tenant.name} />
              </FormField>
              <FormField label="Reason">
                <Input name="reason" placeholder="Cleanup" />
              </FormField>
            </ActionDialog>
          </div>
        </div>
        <div className="section-card__body">
          <div className="kv-grid">
            <div>
              <div className="kv-label">Suspended at</div>
              <div className="kv-value">{dateTime(tenant.suspended_at)}</div>
            </div>
            <div>
              <div className="kv-label">Archived at</div>
              <div className="kv-value">{dateTime(tenant.archived_at)}</div>
            </div>
            <div>
              <div className="kv-label">Data separation</div>
              <div className="kv-value">{titleCase(tenant.isolation_strategy)}</div>
            </div>
            <div>
              <div className="kv-label">Owner</div>
              <div className="kv-value mono">{tenant.owner_id ?? "Unassigned"}</div>
            </div>
          </div>
        </div>
      </section>

      <section className="split-grid">
        <div className="section-card">
          <div className="section-card__head">
            <div>
              <h2>Usage against plan</h2>
              <p>Usage is checked against the limits in the workspace plan.</p>
            </div>
          </div>
          <div className="section-card__body quota-list">
            {tenant.usage.length === 0 ? (
              <p className="small muted" style={{ margin: 0 }}>
                No usage has been reported for the current period.
              </p>
            ) : (
              tenant.usage.map((line) => (
                <div key={line.metric}>
                  <div className="quota-list__head">
                    <strong>{metricLabel(line.metric)}</strong>
                    <span className="muted">
                      {number(line.used)} / {number(line.limit)} · {percent(line.percent)}
                    </span>
                  </div>
                  <div className="progress-track">
                    <div
                      className="progress-fill"
                      style={{ width: `${Math.min(100, line.percent)}%` }}
                      data-over={String(line.over_limit)}
                    />
                  </div>
                  {line.over_limit ? (
                    <p className="small" style={{ color: "var(--ui-p-red-400)", margin: "6px 0 0" }}>
                      Limit reached. New usage is blocked until the period resets.
                    </p>
                  ) : null}
                </div>
              ))
            )}
          </div>
        </div>

        <div className="section-card">
          <div className="section-card__head">
            <div>
              <h2>Subscription</h2>
              <p>Billing details for this workspace.</p>
            </div>
            {tenant.subscription ? (
              <ActionDialog
                label="Change"
                triggerVariant="outline"
                icon={<Pencil size={14} />}
                title="Change subscription"
                description="Changes to the plan, interval, status and discount are recorded in the activity log."
                action={changeSubscriptionAction}
                submitLabel="Update subscription"
              >
                <input type="hidden" name="id" value={tenant.subscription.id} />
                <div className="form-row">
                  <FormField label="Plan">
                    <Select name="plan_id" defaultValue={tenant.subscription.plan_id}>
                      {plans.map((plan) => (
                        <option key={plan.id} value={plan.id}>
                          {plan.name} · {money(plan.price_monthly_cents)}/mo
                        </option>
                      ))}
                    </Select>
                  </FormField>
                  <FormField label="Billing interval">
                    <Select
                      name="billing_interval"
                      defaultValue={tenant.subscription.billing_interval}
                    >
                      <option value="monthly">Monthly</option>
                      <option value="yearly">Yearly</option>
                    </Select>
                  </FormField>
                </div>
                <div className="form-row">
                  <FormField label="Status">
                    <Select name="status" defaultValue={tenant.subscription.status}>
                      <option value="active">Active</option>
                      <option value="trialing">Trial</option>
                      <option value="past_due">Past due</option>
                      <option value="paused">Paused</option>
                      <option value="canceled">Cancelled</option>
                    </Select>
                  </FormField>
                  <FormField label="Discount %">
                    <Input
                      name="discount_percent"
                      type="number"
                      min={0}
                      max={100}
                      defaultValue={tenant.subscription.discount_percent}
                    />
                  </FormField>
                </div>
                <FormField label="Billing">
                  <Checkbox
                    name="cancel_at_period_end"
                    defaultChecked={tenant.subscription.cancel_at_period_end}
                    label="Cancel at the end of the current period"
                  />
                </FormField>
                <FormField label="Reason">
                  <Input name="reason" placeholder="Upgrade to Growth" />
                </FormField>
              </ActionDialog>
            ) : null}
          </div>
          <div className="section-card__body">
            {tenant.subscription ? (
              <div className="stack" style={{ gap: 14 }}>
                <div className="kv-grid">
                  <div>
                    <div className="kv-label">Plan</div>
                    <div className="kv-value">{tenant.subscription.plan_name}</div>
                  </div>
                  <div>
                    <div className="kv-label">Status</div>
                    <div className="kv-value">
                      <StatusBadge status={tenant.subscription.status} />
                    </div>
                  </div>
                  <div>
                    <div className="kv-label">MRR</div>
                    <div className="kv-value">{money(tenant.subscription.mrr_cents)}</div>
                  </div>
                  <div>
                    <div className="kv-label">Interval</div>
                    <div className="kv-value">{titleCase(tenant.subscription.billing_interval)}</div>
                  </div>
                  <div>
                    <div className="kv-label">Current period</div>
                    <div className="kv-value">
                      {date(tenant.subscription.current_period_start)} →{" "}
                      {date(tenant.subscription.current_period_end)}
                    </div>
                  </div>
                  <div>
                    <div className="kv-label">Cancel at period end</div>
                    <div className="kv-value">
                      {tenant.subscription.cancel_at_period_end ? "Yes" : "No"}
                    </div>
                  </div>
                </div>
                {tenant.invoices.length ? (
                  <div>
                    <div className="kv-label" style={{ marginBottom: 8 }}>
                      Latest invoices
                    </div>
                    <div className="stack" style={{ gap: 8 }}>
                      {tenant.invoices.slice(0, 3).map((invoice) => (
                        <div className="alert-row" key={invoice.id}>
                          <div className="alert-row__meta">
                            <strong className="mono">{invoice.number}</strong>
                            <span>
                              {money(invoice.amount_cents, invoice.currency)} · due{" "}
                              {date(invoice.due_at)}
                            </span>
                          </div>
                          <StatusBadge status={invoice.status} />
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}
              </div>
            ) : (
              <EmptyState
                title="No subscription"
                description="Attach a plan to enable billing, limits and trial automation."
              />
            )}
          </div>
        </div>
      </section>

      <section className="section-card">
        <div className="section-card__head">
          <div>
            <h2>Workspace users</h2>
            <p>Membership and roles are limited to this workspace.</p>
          </div>
          <ActionDialog
            label="Invite user"
            triggerVariant="outline"
            icon={<UserPlus size={15} />}
            title="Invite a workspace user"
            description="The user is added with the selected role and receives an email invitation."
            action={inviteTenantUserAction}
            submitLabel="Send invitation"
            closeOnSuccess={false}
          >
            <input type="hidden" name="id" value={tenant.id} />
            <div className="form-row">
              <FormField label="Email" required>
                <Input name="email" type="email" required placeholder="person@example.com" />
              </FormField>
              <FormField label="Full name">
                <Input name="full_name" placeholder="Alex Morgan" />
              </FormField>
            </div>
            <FormField label="Role">
              <Select name="role_key" defaultValue="tenant_member">
                {TENANT_ROLES.map((role) => (
                  <option key={role.key} value={role.key}>
                    {role.label}
                  </option>
                ))}
              </Select>
            </FormField>
            <FormField label="Delivery">
              <Checkbox name="send_email" defaultChecked label="Send invitation email" />
            </FormField>
          </ActionDialog>
        </div>
        {tenant.users.length === 0 ? (
          <EmptyState
            title="No workspace users"
            description="Invite the first administrator so this workspace has a management contact."
          />
        ) : (
          <div className="data-table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>User</th>
                  <th>Roles</th>
                  <th>Membership</th>
                  <th>MFA</th>
                  <th>Last login</th>
                  <th>Joined</th>
                  <th className="align-end">Manage</th>
                </tr>
              </thead>
              <tbody>
                {tenant.users.map((user) => (
                  <tr key={user.id}>
                    <td>
                      <span className="cell-primary">{user.full_name}</span>
                      <span className="cell-sub">{user.email}</span>
                    </td>
                    <td>
                      <div className="pill-list">
                        {(user.role_names ?? user.roles ?? []).map((role) => (
                          <span className="tag-mono" key={role}>
                            {role}
                          </span>
                        ))}
                      </div>
                    </td>
                    <td>
                      <StatusBadge status={user.membership_status} />
                      {user.is_billing_contact ? (
                        <span className="cell-sub">Billing contact</span>
                      ) : null}
                    </td>
                    <td>{user.mfa_enabled ? <Badge variant="success" size="sm">Enabled</Badge> : "—"}</td>
                    <td className="muted">{dateTime(user.last_login_at)}</td>
                    <td className="muted">{date(user.joined_at)}</td>
                    <td className="align-end">
                      <div className="row" style={{ justifyItems: "flex-end", justifyContent: "flex-end", gap: 6 }}>
                        <ActionDialog
                          label="Edit"
                          triggerVariant="ghost"
                          triggerSize="sm"
                          title={`Manage ${user.full_name}`}
                          description="Role and membership changes are recorded in the activity log."
                          action={updateTenantUserAction}
                          submitLabel="Update user"
                        >
                          <input type="hidden" name="id" value={tenant.id} />
                          <input type="hidden" name="user_id" value={user.id} />
                          <FormField label="Role">
                            <Select
                              name="role_key"
                              defaultValue={(user.roles ?? [])[0] ?? "tenant_member"}
                            >
                              {TENANT_ROLES.map((role) => (
                                <option key={role.key} value={role.key}>
                                  {role.label}
                                </option>
                              ))}
                            </Select>
                          </FormField>
                          <FormField label="Membership status">
                            <Select name="status" defaultValue={user.membership_status}>
                              <option value="active">Active</option>
                              <option value="suspended">Suspended</option>
                              <option value="invited">Invited</option>
                            </Select>
                          </FormField>
                        </ActionDialog>
                        <ActionDialog
                          label="Remove"
                          triggerVariant="ghost"
                          triggerSize="sm"
                          title={`Remove ${user.full_name}`}
                          description="The user loses access to this workspace immediately. Platform access is unaffected."
                          action={removeTenantUserAction}
                          submitLabel="Remove user"
                          destructive
                        >
                          <input type="hidden" name="id" value={tenant.id} />
                          <input type="hidden" name="user_id" value={user.id} />
                        </ActionDialog>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="detail-grid">
        <div className="section-card">
          <div className="section-card__head">
            <div>
              <h2>Domains</h2>
              <p>Verified domains give this workspace a branded address.</p>
            </div>
            <ActionDialog
              label="Add domain"
              triggerVariant="outline"
              icon={<Globe size={15} />}
              title="Add custom domain"
              action={addTenantDomainAction}
              submitLabel="Add domain"
            >
              <input type="hidden" name="id" value={tenant.id} />
              <FormField label="Domain" required>
                <Input name="domain" placeholder="tenant.example.com" required />
              </FormField>
              <FormField label="Primary">
                <Checkbox name="is_primary" label="Use as the primary workspace domain" />
              </FormField>
            </ActionDialog>
          </div>
          {tenant.domains.length === 0 ? (
            <EmptyState
              title="No custom domains"
              description="The workspace resolves by slug. Add a domain for branded entry points."
            />
          ) : (
            <div className="section-card__body stack">
              {tenant.domains.map((domain) => (
                <div className="alert-row" key={domain.id}>
                  <div className="alert-row__meta">
                    <strong className="mono">{domain.domain}</strong>
                    <span>
                      {domain.status} · SSL {domain.ssl_status}
                      {domain.is_primary ? " · primary" : ""}
                    </span>
                    {domain.verification_token ? (
                      <span className="mono">TXT: {domain.verification_token}</span>
                    ) : null}
                  </div>
                  <div className="row" style={{ gap: 6 }}>
                    <StatusBadge status={domain.status} />
                    {domain.status !== "verified" ? (
                      <QuickActionForm
                        action={verifyTenantDomainAction}
                        fields={{ id: tenant.id, domain_id: domain.id }}
                        label="Verify"
                        variant="secondary"
                      />
                    ) : (
                      <BadgeCheck size={18} color="var(--ui-p-green-400)" />
                    )}
                    <ActionDialog
                      label="Delete"
                      triggerVariant="ghost"
                      triggerSize="sm"
                      title="Delete domain"
                      description="The workspace will stop resolving through this domain."
                      action={deleteTenantDomainAction}
                      submitLabel="Delete domain"
                      destructive
                    >
                      <input type="hidden" name="id" value={tenant.id} />
                      <input type="hidden" name="domain_id" value={domain.id} />
                    </ActionDialog>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="section-card">
          <div className="section-card__head">
            <div>
              <h2>Included features</h2>
              <p>Features available to this workspace, including overrides.</p>
            </div>
          </div>
          <div className="section-card__body stack">
            {tenant.features.length === 0 ? (
              <p className="small muted" style={{ margin: 0 }}>
                No feature flags are defined for the parent application.
              </p>
            ) : (
              tenant.features.map((feature) => (
                <div className="alert-row" key={feature.id}>
                  <div className="alert-row__meta">
                    <strong>
                      {feature.name} <span className="tag-mono">{feature.key}</span>
                    </strong>
                    <span>
                      Rollout {feature.rollout_percentage}%
                      {feature.override !== null
                        ? ` · workspace override ${feature.override ? "on" : "off"}`
                        : ""}
                    </span>
                  </div>
                  <StatusBadge status={feature.enabled ? "enabled" : "inactive"} />
                </div>
              ))
            )}
          </div>
        </div>
      </section>

      <section className="section-card">
        <div className="section-card__head">
          <div>
            <h2>Workspace settings</h2>
            <p>Settings your application reads at runtime.</p>
          </div>
          <ActionDialog
            label="Publish settings"
            triggerVariant="outline"
            icon={<Plus size={15} />}
            title="Publish workspace settings"
            description="Creates a new version of the settings for this workspace."
            action={publishTenantConfigAction}
            submitLabel="Publish version"
            size="lg"
          >
            <input type="hidden" name="id" value={tenant.id} />
            <FormField label="Configuration JSON" required>
              <Textarea
                name="config"
                rows={8}
                defaultValue={JSON.stringify(tenant.configuration, null, 2)}
              />
            </FormField>
            <FormField label="Change note">
              <Input name="note" placeholder="Enable new onboarding flow" />
            </FormField>
          </ActionDialog>
        </div>
        <div className="detail-grid" style={{ padding: 20 }}>
          <div>
            <div className="kv-label" style={{ marginBottom: 8 }}>
              Current values
            </div>
            <pre className="code-block">{JSON.stringify(tenant.configuration, null, 2)}</pre>
          </div>
          <div className="stack" style={{ gap: 10 }}>
            <div className="kv-label">History</div>
            {tenant.config_history.length === 0 ? (
              <p className="small muted">No published versions.</p>
            ) : (
              tenant.config_history.map((version) => (
                <div className="alert-row" key={version.id}>
                  <div className="alert-row__meta">
                    <strong>Version {version.version}</strong>
                    <span>{dateTime(version.created_at)}</span>
                    <span>{version.note ?? "No change note"}</span>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      </section>

      <section className="section-card">
        <div className="section-card__head">
          <div>
            <h2>Workspace activity</h2>
            <p>Changes made in this workspace.</p>
          </div>
          <Link className="small muted" href={`/audit?scope_type=tenant&scope_id=${tenant.id}`}>
            Open in activity log
          </Link>
        </div>
        <div className="section-card__body">
          {tenant.activity.length === 0 ? (
            <p className="small muted" style={{ margin: 0 }}>
              No activity recorded yet.
            </p>
          ) : (
            <div className="timeline">
              {tenant.activity.map((entry) => {
                const record = entry as Record<string, unknown>;
                return (
                  <div className="timeline__item" key={String(record.id)}>
                    <span className="timeline__dot" />
                    <div className="timeline__body">
                      <strong className="audit-action">{actionLabel(String(record.action))}</strong>
                      <span>
                        {String(record.actor_email ?? "system")} ·{" "}
                        {relativeTime(String(record.created_at))} ·{" "}
                        {titleCase(String(record.outcome ?? "success"))}
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
