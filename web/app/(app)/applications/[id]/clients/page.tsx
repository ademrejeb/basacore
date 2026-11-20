import Link from "next/link";
import { notFound } from "next/navigation";
import { ArrowUpRight, Plus, Users } from "lucide-react";

import { Badge, FormField, Input, Select } from "@adem.rejeb/ui";

import { createTenantAction } from "@/app/actions";
import { ActionDialog } from "@/components/action-dialog";
import { EmptyState } from "@/components/pagination";
import { ProductHeader } from "@/components/product-header";
import { StatusBadge } from "@/components/status-badge";
import { apiFetch, ApiError } from "@/lib/api";
import { date, money, number } from "@/lib/format";
import type { ApplicationDetail, Organization, Page, Plan, Tenant } from "@/lib/types";

export const metadata = { title: "Workspaces · Application" };

type Params = Promise<{ id: string }>;

export default async function ProductWorkspacesPage({ params }: { params: Params }) {
  const { id } = await params;

  let application: ApplicationDetail;
  let tenants: Page<Tenant>;
  let organizations: Page<Organization>;
  let plans: Plan[];
  try {
    [application, tenants, organizations, plans] = await Promise.all([
      apiFetch<ApplicationDetail>(`/v1/applications/${id}`),
      apiFetch<Page<Tenant>>(`/v1/tenants?application_id=${id}&limit=100`),
      apiFetch<Page<Organization>>("/v1/organizations?limit=200"),
      apiFetch<Plan[]>("/v1/billing/plans"),
    ]);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) notFound();
    throw error;
  }

  const createFields = (
    <>
      <input type="hidden" name="application_id" value={application.id} />
      <FormField label="Organization" required>
        <Select name="organization_id" placeholder="Select an organization" required>
          {organizations.items.map((organization) => (
            <option key={organization.id} value={organization.id}>
              {organization.name}
            </option>
          ))}
        </Select>
      </FormField>
      <div className="form-row">
        <FormField label="Workspace name" required>
          <Input name="name" placeholder="Northwind Sales" required />
        </FormField>
        <FormField label="Short name" description="Generated from the name when left blank.">
          <Input name="slug" placeholder="northwind-sales" pattern="[a-z0-9-]+" />
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
        description="Optional. Creates the first workspace owner and sends an invitation."
      >
        <Input name="owner_email" type="email" placeholder="owner@example.com" />
      </FormField>
    </>
  );

  return (
    <div className="page-stack">
      <ProductHeader application={application} active="workspaces" />

      <section className="section-card">
        <div className="section-card__head">
          <div>
            <h2>Workspaces for {application.name}</h2>
            <p>
              {number(tenants.total)} {tenants.total === 1 ? "workspace" : "workspaces"}.
              Each organization gets one separate workspace per application, with its own
              users, roles, plan and usage.
            </p>
          </div>
          <ActionDialog
            label="Add workspace"
            triggerVariant="primary"
            icon={<Plus size={15} />}
            title={`Add a workspace to ${application.name}`}
            description="This creates the workspace, the subscription and an optional first owner."
            action={createTenantAction}
            submitLabel="Add workspace"
            closeOnSuccess={false}
            size="xl"
          >
            {createFields}
          </ActionDialog>
        </div>

        {tenants.items.length === 0 ? (
          <EmptyState
            title="No workspaces yet"
            description="Add a workspace so an organization can sign in to this application."
            action={
              <ActionDialog
                label="Add the first workspace"
                icon={<Users size={15} />}
                title={`Add a workspace to ${application.name}`}
                action={createTenantAction}
                submitLabel="Add workspace"
                closeOnSuccess={false}
                size="xl"
              >
                {createFields}
              </ActionDialog>
            }
          />
        ) : (
          <div className="data-table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th scope="col">Workspace</th>
                  <th scope="col">Organization</th>
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
                      {tenant.organization_name ? (
                        <Link href={`/organizations/${tenant.organization_id}`}>
                          {tenant.organization_name}
                        </Link>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td>
                      <StatusBadge status={tenant.status} />
                    </td>
                    <td>
                      {tenant.plan_name ?? "—"}
                      <span className="cell-sub">{tenant.subscription_status ?? "No plan"}</span>
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
            <h2>Sign-in rules</h2>
            <p>How Basacore decides whether someone can enter this application.</p>
          </div>
          <Badge variant="outline" size="sm">
            Enforced automatically
          </Badge>
        </div>
        <div className="section-card__body stack">
          {[
            "Every sign-in is linked to exactly one workspace.",
            "If the application asks for a workspace, the person's membership is checked first.",
            "Suspended or archived workspaces cannot sign in.",
            "Workspaces from another application are rejected.",
          ].map((rule) => (
            <div className="alert-row" key={rule}>
              <div className="alert-row__meta">
                <span>{rule}</span>
              </div>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
