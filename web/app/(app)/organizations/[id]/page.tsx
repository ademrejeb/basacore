import Link from "next/link";
import { notFound } from "next/navigation";
import { ArrowUpRight, Pencil, Plus, Users } from "lucide-react";

import { Badge, FormField, Input, Select, Textarea } from "@adem.rejeb/ui";

import { createTenantAction, updateOrganizationAction } from "@/app/actions";
import { ActionDialog } from "@/components/action-dialog";
import { EmptyState } from "@/components/pagination";
import { StatusBadge } from "@/components/status-badge";
import { apiFetch, ApiError } from "@/lib/api";
import { date, dateTime, money, number, statusLabel } from "@/lib/format";
import type { Application, OrganizationDetail, Page, Plan, Tenant } from "@/lib/types";

export const metadata = { title: "Organization" };

type Params = Promise<{ id: string }>;

export default async function OrganizationDetailPage({ params }: { params: Params }) {
  const { id } = await params;

  let organization: OrganizationDetail;
  let tenants: Page<Tenant>;
  let applications: Page<Application>;
  let plans: Plan[];
  try {
    [organization, tenants, applications, plans] = await Promise.all([
      apiFetch<OrganizationDetail>(`/v1/organizations/${id}`),
      apiFetch<Page<Tenant>>(`/v1/tenants?organization_id=${id}&limit=100`),
      apiFetch<Page<Application>>("/v1/applications?limit=200"),
      apiFetch<Plan[]>("/v1/billing/plans"),
    ]);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) notFound();
    throw error;
  }

  const initials = organization.name
    .split(/\s+/)
    .slice(0, 2)
    .map((word) => word[0]?.toUpperCase())
    .join("");

  const accessFields = (
    <>
      <input type="hidden" name="organization_id" value={organization.id} />
      <FormField label="Application" required>
        <Select name="application_id" placeholder="Select an application" required>
          {applications.items.map((application) => (
            <option key={application.id} value={application.id}>
              {application.name}
            </option>
          ))}
        </Select>
      </FormField>
      <div className="form-row">
        <FormField label="Workspace name" required>
          <Input name="name" defaultValue={organization.name} required />
        </FormField>
        <FormField label="Short name" description="Generated from the name when left blank.">
          <Input
            name="slug"
            defaultValue={organization.slug}
            pattern="[a-z0-9-]+"
          />
        </FormField>
      </div>
      <div className="form-row">
        <FormField label="Plan">
          <Select name="plan_id" defaultValue="">
            <option value="">No plan</option>
            {plans.map((plan) => (
              <option key={plan.id} value={plan.id}>
                {plan.name} · {money(plan.price_monthly_cents)}/mo
              </option>
            ))}
          </Select>
        </FormField>
        <FormField label="Trial days">
          <Input name="trial_days" type="number" min={0} max={365} defaultValue={14} />
        </FormField>
      </div>
      <FormField
        label="Owner email"
        description="Optional. Adds the first workspace owner and returns an invitation."
      >
        <Input name="owner_email" type="email" placeholder="owner@example.com" />
      </FormField>
    </>
  );

  return (
    <div className="page-stack">
      <div className="detail-hero">
        <span className="detail-hero__mark">{initials}</span>
        <div style={{ flex: 1 }}>
          <div className="row" style={{ gap: 10 }}>
            <h1>{organization.name}</h1>
            <StatusBadge status={organization.status} />
            <Badge variant="outline" size="sm">
              Organization
            </Badge>
          </div>
          <p>{organization.description ?? "No description has been recorded for this organization."}</p>
          <div className="row small muted" style={{ marginTop: 10, gap: 14 }}>
            <span className="mono">{organization.slug}</span>
            <span>Created {date(organization.created_at)}</span>
            <span>Updated {dateTime(organization.updated_at)}</span>
          </div>
        </div>
        <div className="page-heading__actions">
          <Link className="ui-button ui-button--outline" href="/organizations">
            All organizations
          </Link>
          <ActionDialog
            label="Edit"
            triggerVariant="secondary"
            icon={<Pencil size={15} />}
            title="Edit organization"
            description="Changes are recorded in the activity log."
            action={updateOrganizationAction}
            submitLabel="Save changes"
          >
            <input type="hidden" name="id" value={organization.id} />
            <FormField label="Name" required>
              <Input name="name" defaultValue={organization.name} required />
            </FormField>
            <div className="form-row">
              <FormField label="Billing email">
                <Input
                  name="billing_email"
                  type="email"
                  defaultValue={organization.billing_email ?? ""}
                />
              </FormField>
              <FormField label="Status">
                <Select name="status" defaultValue={organization.status}>
                  <option value="active">Active</option>
                  <option value="suspended">Suspended</option>
                  <option value="archived">Archived</option>
                </Select>
              </FormField>
            </div>
            <FormField label="Description">
              <Textarea name="description" rows={3} defaultValue={organization.description ?? ""} />
            </FormField>
          </ActionDialog>
        </div>
      </div>

      <section className="metric-grid">
        <div className="section-card">
          <div className="section-card__body">
            <div className="kv-label">Applications</div>
            <div className="stat-inline">
              <strong>{number(organization.counts.applications)}</strong>
              <span>registered</span>
            </div>
          </div>
        </div>
        <div className="section-card">
          <div className="section-card__body">
            <div className="kv-label">Workspaces</div>
            <div className="stat-inline">
              <strong>{number(tenants.total)}</strong>
              <span>active</span>
            </div>
          </div>
        </div>
        <div className="section-card">
          <div className="section-card__body">
            <div className="kv-label">Users</div>
            <div className="stat-inline">
              <strong>{number(organization.counts.users)}</strong>
              <span>members</span>
            </div>
          </div>
        </div>
        <div className="section-card">
          <div className="section-card__body">
            <div className="kv-label">Billing</div>
            <div className="stat-inline">
              <strong>{organization.billing_email ? "Set" : "Missing"}</strong>
              <span>{organization.billing_email ?? "Add a billing contact"}</span>
            </div>
          </div>
        </div>
      </section>

      <section className="section-card">
        <div className="section-card__head">
          <div>
            <h2>Workspaces</h2>
            <p>
              Each row is this organization&apos;s separate workspace for one
              application, with its own users, roles, plan and usage.
            </p>
          </div>
          <div className="row" style={{ gap: 8 }}>
            <Link
              className="ui-button ui-button--outline ui-button--sm"
              href={`/tenants?organization_id=${organization.id}`}
            >
              All workspaces
            </Link>
            <ActionDialog
              label="Add workspace"
              triggerVariant="primary"
              icon={<Plus size={15} />}
              title={`Add a workspace for ${organization.name}`}
              description="This creates the workspace, the subscription and an optional first owner."
              action={createTenantAction}
              submitLabel="Add workspace"
              size="xl"
            >
              {accessFields}
            </ActionDialog>
          </div>
        </div>
        {tenants.items.length === 0 ? (
          <EmptyState
            title="No workspaces yet"
            description="Add a workspace to give this organization access to an application."
            action={
              <ActionDialog
                label="Add the first workspace"
                icon={<Users size={15} />}
                title={`Add a workspace for ${organization.name}`}
                action={createTenantAction}
                submitLabel="Add workspace"
                size="xl"
              >
                {accessFields}
              </ActionDialog>
            }
          />
        ) : (
          <div className="data-table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th scope="col">Workspace</th>
                  <th scope="col">Application</th>
                  <th scope="col">Status</th>
                  <th scope="col">Plan</th>
                  <th scope="col">Users</th>
                  <th scope="col">MRR</th>
                  <th scope="col">Created</th>
                  <th scope="col" className="align-end">
                    Open
                  </th>
                </tr>
              </thead>
              <tbody>
                {tenants.items.map((tenant) => (
                  <tr key={tenant.id}>
                    <td>
                      <span className="cell-primary">
                        <Link href={`/tenants/${tenant.id}`}>{tenant.name}</Link>
                      </span>
                      <span className="cell-sub mono">{tenant.slug}</span>
                    </td>
                    <td>
                      <Link href={`/applications/${tenant.application_id}`}>
                        {tenant.application_name ?? "View application"}
                      </Link>
                    </td>
                    <td>
                      <StatusBadge status={tenant.status} />
                    </td>
                    <td>
                      {tenant.plan_name ?? "—"}
                      <span className="cell-sub">
                        {tenant.subscription_status ? statusLabel(tenant.subscription_status) : "No plan"}
                      </span>
                    </td>
                    <td>{number(tenant.users)}</td>
                    <td>{tenant.mrr_cents ? money(tenant.mrr_cents) : "—"}</td>
                    <td className="muted">{date(tenant.created_at)}</td>
                    <td className="align-end">
                      <Link className="pagination__link" href={`/tenants/${tenant.id}`}>
                        Open <ArrowUpRight size={13} />
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="section-card">
        <div className="section-card__head">
          <div>
            <h2>Organization details</h2>
            <p>Billing contact and record details.</p>
          </div>
          <Badge variant="outline" size="sm">
            {organization.billing_email ? "Billing configured" : "Billing contact missing"}
          </Badge>
        </div>
        <div className="section-card__body">
          <div className="kv-grid">
            <div>
              <div className="kv-label">Billing email</div>
              <div className="kv-value">{organization.billing_email ?? "—"}</div>
            </div>
            <div>
              <div className="kv-label">Organization ID</div>
              <div className="kv-value mono">{organization.id}</div>
            </div>
            <div>
              <div className="kv-label">Created</div>
              <div className="kv-value">{dateTime(organization.created_at)}</div>
            </div>
            {Object.keys(organization.settings).length ? (
              <div>
                <div className="kv-label">Custom settings</div>
                <div className="pill-list">
                  {Object.entries(organization.settings).map(([key, value]) => (
                    <span className="tag-mono" key={key}>
                      {key}:{" "}
                      {typeof value === "object" && value !== null
                        ? JSON.stringify(value)
                        : String(value)}
                    </span>
                  ))}
                </div>
              </div>
            ) : null}
          </div>
        </div>
      </section>
    </div>
  );
}
