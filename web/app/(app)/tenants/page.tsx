import Link from "next/link";
import { Plus, Users } from "lucide-react";

import { Button, FormField, Input, Select } from "@adem.rejeb/ui";

import { createTenantAction } from "@/app/actions";
import { ActionDialog } from "@/components/action-dialog";
import { EmptyState, Pagination } from "@/components/pagination";
import { StatusBadge } from "@/components/status-badge";
import { apiFetch, queryString } from "@/lib/api";
import { date, money, number, statusLabel } from "@/lib/format";
import type { Application, Organization, Page, Plan, Tenant } from "@/lib/types";

export const metadata = { title: "Workspaces" };

type SearchParams = Promise<{
  search?: string;
  status?: string;
  application_id?: string;
  organization_id?: string;
  offset?: string;
}>;

export default async function WorkspacesPage({ searchParams }: { searchParams: SearchParams }) {
  const params = await searchParams;
  const search = params.search?.trim() ?? "";
  const status = params.status ?? "";
  const applicationId = params.application_id ?? "";
  const organizationId = params.organization_id ?? "";
  const offset = Number(params.offset ?? 0) || 0;

  const [page, applications, organizations, plans] = await Promise.all([
    apiFetch<Page<Tenant>>(
      `/v1/tenants${queryString({
        search,
        tenant_status: status,
        application_id: applicationId,
        organization_id: organizationId,
        limit: 20,
        offset,
      })}`,
    ),
    apiFetch<Page<Application>>("/v1/applications?limit=200"),
    apiFetch<Page<Organization>>("/v1/organizations?limit=200"),
    apiFetch<Plan[]>("/v1/billing/plans"),
  ]);

  const createFields = (
    <>
      <div className="form-row">
        <FormField label="Organization" required>
          <Select name="organization_id" placeholder="Select an organization" required>
            {organizations.items.map((organization) => (
              <option key={organization.id} value={organization.id}>
                {organization.name}
              </option>
            ))}
          </Select>
        </FormField>
        <FormField label="Application" required>
          <Select name="application_id" placeholder="Select an application" required>
            {applications.items.map((application) => (
              <option key={application.id} value={application.id}>
                {application.name}
              </option>
            ))}
          </Select>
        </FormField>
      </div>
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
      <div className="page-heading">
        <div>
          <span className="eyebrow">Workspaces</span>
          <h1>Workspaces</h1>
          <p>
            Each workspace is a separate, private space for one organization inside one
            application. It has its own users, roles, plan and usage.
          </p>
        </div>
        <div className="page-heading__actions">
          <ActionDialog
            label="Add workspace"
            icon={<Plus size={15} />}
            title="Add a workspace"
            description="Creates a separate workspace, a subscription and an optional first owner."
            action={createTenantAction}
            submitLabel="Add workspace"
            closeOnSuccess={false}
            size="xl"
          >
            {createFields}
          </ActionDialog>
        </div>
      </div>

      <div className="data-card">
        <div className="data-card__head data-card__head--stack">
          <div>
            <h2>
              {number(page.total)} {page.total === 1 ? "workspace" : "workspaces"}
            </h2>
            <p>Filter by status, organization or application.</p>
          </div>
          <form action="/tenants" method="get" className="filter-bar">
            <Input
              className="filter-input"
              name="search"
              defaultValue={search}
              placeholder="Search workspaces…"
              aria-label="Search workspaces"
            />
            <Select name="status" defaultValue={status} aria-label="Workspace status">
              <option value="">All statuses</option>
              <option value="provisioning">Setting up</option>
              <option value="trial">Trial</option>
              <option value="active">Active</option>
              <option value="suspended">Suspended</option>
              <option value="archived">Archived</option>
              <option value="deleted">Deleted</option>
            </Select>
            <Select name="application_id" defaultValue={applicationId} aria-label="Application">
              <option value="">All applications</option>
              {applications.items.map((application) => (
                <option key={application.id} value={application.id}>
                  {application.name}
                </option>
              ))}
            </Select>
            <Select name="organization_id" defaultValue={organizationId} aria-label="Organization">
              <option value="">All organizations</option>
              {organizations.items.map((organization) => (
                <option key={organization.id} value={organization.id}>
                  {organization.name}
                </option>
              ))}
            </Select>
            <Button type="submit" variant="secondary" size="sm">
              Filter
            </Button>
            {search || status || applicationId || organizationId ? (
              <Link className="pagination__link" href="/tenants">
                Reset
              </Link>
            ) : null}
          </form>
        </div>

        {page.items.length === 0 ? (
          <EmptyState
            title={search || status || applicationId ? "No workspaces match" : "No workspaces yet"}
            description="Add a workspace to let an organization sign in to one of your applications."
            action={
              <ActionDialog
                label="Add the first workspace"
                icon={<Users size={15} />}
                title="Add a workspace"
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
          <>
            <div className="data-table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th scope="col">Workspace</th>
                    <th scope="col">Organization</th>
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
                  {page.items.map((tenant) => (
                    <tr key={tenant.id}>
                      <td>
                        <span className="cell-primary">
                          <Link href={`/tenants/${tenant.id}`}>{tenant.name}</Link>
                        </span>
                        <span className="cell-sub mono">{tenant.slug}</span>
                      </td>
                      <td>
                        <Link href={`/organizations/${tenant.organization_id}`}>
                          {tenant.organization_name ?? "View organization"}
                        </Link>
                      </td>
                      <td>
                        <Link href={`/applications/${tenant.application_id}`}>
                          {tenant.application_name ?? "—"}
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
                          Open
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pagination
              total={page.total}
              limit={page.limit}
              offset={page.offset}
              params={{
                search,
                status,
                application_id: applicationId,
                organization_id: organizationId,
              }}
            />
          </>
        )}
      </div>
    </div>
  );
}
