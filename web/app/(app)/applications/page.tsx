import Link from "next/link";
import { ArrowUpRight, PlugZap, Plus } from "lucide-react";

import { Button, FormField, Input, Select, Textarea } from "@adem.rejeb/ui";

import { createApplicationAction } from "@/app/actions";
import { ActionDialog } from "@/components/action-dialog";
import { EmptyState, Pagination } from "@/components/pagination";
import { StatusBadge } from "@/components/status-badge";
import { apiFetch, queryString } from "@/lib/api";
import { date, number } from "@/lib/format";
import type { Application, Organization, Page } from "@/lib/types";

export const metadata = { title: "Applications" };

type SearchParams = Promise<{
  search?: string;
  status?: string;
  organization_id?: string;
  offset?: string;
}>;

function CreateApplicationDialog({
  organizations,
  variant = "primary",
  label = "Add application",
}: {
  organizations: Organization[];
  variant?: "primary" | "outline";
  label?: string;
}) {
  return (
    <ActionDialog
      label={label}
      triggerVariant={variant}
      icon={<Plus size={15} />}
      title="Add an application"
      description="An application is what your organizations sign in to. A production environment is created automatically."
      action={createApplicationAction}
      submitLabel="Add application"
      size="lg"
    >
      <FormField label="Owning organization" required>
        <Select name="organization_id" placeholder="Select an organization" required>
          {organizations.map((organization) => (
            <option key={organization.id} value={organization.id}>
              {organization.name}
            </option>
          ))}
        </Select>
      </FormField>
      <div className="form-row">
        <FormField label="Application name" required>
          <Input name="name" placeholder="Atlas CRM" required />
        </FormField>
        <FormField label="Short name" description="Used in links. Generated from the name when left blank.">
          <Input name="slug" placeholder="atlas-crm" pattern="[a-z0-9-]+" />
        </FormField>
      </div>
      <FormField label="Base URL">
        <Input name="base_url" type="url" placeholder="https://app.example.com" />
      </FormField>
      <FormField label="Description">
        <Textarea name="description" rows={2} placeholder="What this application is for." />
      </FormField>
      <div className="form-row">
        <FormField label="Two-factor for end users" description="Applies to people signing in.">
          <Select name="mfa" defaultValue="optional">
            <option value="optional">Optional</option>
            <option value="required">Required</option>
            <option value="disabled">Disabled</option>
          </Select>
        </FormField>
        <FormField label="Session length (minutes)">
          <Input name="session_ttl_minutes" type="number" min={5} defaultValue={60} />
        </FormField>
      </div>
    </ActionDialog>
  );
}

export default async function ApplicationsPage({ searchParams }: { searchParams: SearchParams }) {
  const params = await searchParams;
  const search = params.search?.trim() ?? "";
  const status = params.status ?? "";
  const organizationId = params.organization_id ?? "";
  const offset = Number(params.offset ?? 0) || 0;

  const [page, organizations] = await Promise.all([
    apiFetch<Page<Application>>(
      `/v1/applications${queryString({
        search,
        app_status: status,
        organization_id: organizationId,
        limit: 20,
        offset,
      })}`,
    ),
    apiFetch<Page<Organization>>("/v1/organizations?limit=200"),
  ]);

  return (
    <div className="page-stack">
      <div className="page-heading">
        <div>
          <span className="eyebrow">Applications</span>
          <h1>Applications</h1>
          <p>
            The applications your organizations sign in to. Open one to set up sign-in,
            manage workspaces and review its settings.
          </p>
        </div>
        <div className="page-heading__actions">
          <CreateApplicationDialog organizations={organizations.items} />
        </div>
      </div>

      <div className="data-card">
        <div className="data-card__head data-card__head--stack">
          <div>
            <h2>
              {number(page.total)} {page.total === 1 ? "application" : "applications"}
            </h2>
            <p>Search by name or short name, then narrow by status.</p>
          </div>
          <form action="/applications" method="get" className="filter-bar">
            <Input
              className="filter-input"
              name="search"
              defaultValue={search}
              placeholder="Search applications…"
              aria-label="Search applications"
            />
            <Select name="status" defaultValue={status} aria-label="Application status">
              <option value="">All statuses</option>
              <option value="draft">Draft</option>
              <option value="active">Active</option>
              <option value="inactive">Inactive</option>
              <option value="archived">Archived</option>
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
            {search || status || organizationId ? (
              <Link className="pagination__link" href="/applications">
                Reset
              </Link>
            ) : null}
          </form>
        </div>

        {page.items.length === 0 ? (
          <EmptyState
            title={
              search || status || organizationId
                ? "No applications match"
                : "No applications yet"
            }
            description="Add an application to give your organizations a place to sign in, with separate workspaces and usage."
            action={
              <CreateApplicationDialog
                organizations={organizations.items}
                label="Add the first application"
              />
            }
          />
        ) : (
          <>
            <div className="data-table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th scope="col">Application</th>
                    <th scope="col">Organization</th>
                    <th scope="col">Status</th>
                    <th scope="col">Workspaces</th>
                    <th scope="col">Setup</th>
                    <th scope="col">Owner</th>
                    <th scope="col">Created</th>
                    <th scope="col" className="align-end">
                      Actions
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {page.items.map((application) => (
                    <tr key={application.id}>
                      <td>
                        <span className="cell-primary">
                          <Link href={`/applications/${application.id}`}>{application.name}</Link>
                        </span>
                        <span className="cell-sub mono">{application.slug}</span>
                      </td>
                      <td>{application.organization_name ?? "—"}</td>
                      <td>
                        <StatusBadge status={application.status} />
                      </td>
                      <td>
                        {number(application.counts?.tenants ?? 0)}
                        <span className="cell-sub">
                          {number(application.counts?.active ?? 0)} active
                        </span>
                      </td>
                      <td>
                        <Link
                          className="pagination__link"
                          href={`/applications/${application.id}/integration`}
                        >
                          <PlugZap size={13} /> Open setup
                        </Link>
                      </td>
                      <td>{application.owner_name ?? "—"}</td>
                      <td className="muted">{date(application.created_at)}</td>
                      <td className="align-end">
                        <Link
                          className="pagination__link"
                          href={`/applications/${application.id}`}
                        >
                          Open <ArrowUpRight size={13} />
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
              params={{ search, status, organization_id: organizationId }}
            />
          </>
        )}
      </div>
    </div>
  );
}
