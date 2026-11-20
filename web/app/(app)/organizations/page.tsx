import Link from "next/link";
import { Building2, Plus } from "lucide-react";

import { Button, FormField, Input, Textarea } from "@adem.rejeb/ui";

import { createOrganizationAction } from "@/app/actions";
import { ActionDialog } from "@/components/action-dialog";
import { EmptyState, Pagination } from "@/components/pagination";
import { StatusBadge } from "@/components/status-badge";
import { apiFetch, queryString } from "@/lib/api";
import { date, number } from "@/lib/format";
import type { Organization, Page } from "@/lib/types";

export const metadata = { title: "Organizations" };

type SearchParams = Promise<{ search?: string; offset?: string }>;

function CreateOrganizationDialog({
  variant = "primary",
  label = "Add organization",
}: {
  variant?: "primary" | "outline";
  label?: string;
}) {
  return (
    <ActionDialog
      label={label}
      triggerVariant={variant}
      icon={<Plus size={15} />}
      title="Add an organization"
      description="An organization is a company that uses your applications. Each application gets one separate workspace for this organization."
      action={createOrganizationAction}
      submitLabel="Add organization"
    >
      <FormField label="Organization name" required>
        <Input name="name" placeholder="Acme Cloud" minLength={2} required />
      </FormField>
      <div className="form-row">
        <FormField label="Short name" description="Lowercase letters, digits and dashes.">
          <Input name="slug" placeholder="acme-cloud" pattern="[a-z0-9-]+" />
        </FormField>
        <FormField label="Billing email">
          <Input name="billing_email" type="email" placeholder="finance@acme.dev" />
        </FormField>
      </div>
      <FormField label="Description">
        <Textarea name="description" rows={3} placeholder="What this organization does." />
      </FormField>
    </ActionDialog>
  );
}

export default async function OrganizationsPage({ searchParams }: { searchParams: SearchParams }) {
  const params = await searchParams;
  const search = params.search?.trim() ?? "";
  const offset = Number(params.offset ?? 0) || 0;

  const page = await apiFetch<Page<Organization>>(
    `/v1/organizations${queryString({ search, offset, limit: 20 })}`,
  );

  return (
    <div className="page-stack">
      <div className="page-heading">
        <div>
          <span className="eyebrow">Organizations</span>
          <h1>Organizations</h1>
          <p>
            Companies that use your applications. Each organization has one separate
            workspace per application, with its own users, roles, plan and usage.
          </p>
        </div>
        <div className="page-heading__actions">
          <CreateOrganizationDialog />
        </div>
      </div>

      <div className="data-card">
        <div className="data-card__head data-card__head--stack">
          <div>
            <h2>
              {number(page.total)} {page.total === 1 ? "organization" : "organizations"}
            </h2>
            <p>Search by name, short name or billing contact.</p>
          </div>
          <form action="/organizations" method="get" className="filter-bar">
            <Input
              className="filter-input"
              name="search"
              defaultValue={search}
              placeholder="Search organizations…"
              aria-label="Search organizations"
            />
            <Button type="submit" variant="secondary" size="sm">
              Search
            </Button>
            {search ? (
              <Link className="pagination__link" href="/organizations">
                Reset
              </Link>
            ) : null}
          </form>
        </div>

        {page.items.length === 0 ? (
          <EmptyState
            title={search ? "No organizations match that search" : "No organizations yet"}
            description={
              search
                ? "Try a shorter term, or clear the search to see the full list."
                : "Add the first organization, then give it access to an application."
            }
            action={
              <CreateOrganizationDialog
                variant="outline"
                label="Add the first organization"
              />
            }
          />
        ) : (
          <>
            <div className="data-table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th scope="col">Organization</th>
                    <th scope="col">Status</th>
                    <th scope="col">Applications</th>
                    <th scope="col">Workspaces</th>
                    <th scope="col">Users</th>
                    <th scope="col">Billing contact</th>
                    <th scope="col">Created</th>
                    <th scope="col" className="align-end">
                      Open
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {page.items.map((organization) => (
                    <tr key={organization.id}>
                      <td>
                        <span className="cell-primary">
                          <Link href={`/organizations/${organization.id}`}>
                            {organization.name}
                          </Link>
                        </span>
                        <span className="cell-sub mono">{organization.slug}</span>
                      </td>
                      <td>
                        <StatusBadge status={organization.status} />
                      </td>
                      <td>{number(organization.counts?.applications ?? 0)}</td>
                      <td>{number(organization.counts?.tenants ?? 0)}</td>
                      <td>{number(organization.counts?.users ?? 0)}</td>
                      <td>{organization.billing_email ?? <span className="muted">—</span>}</td>
                      <td className="muted">{date(organization.created_at)}</td>
                      <td className="align-end">
                        <Link
                          className="pagination__link"
                          href={`/organizations/${organization.id}`}
                        >
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
              params={{ search }}
            />
          </>
        )}
      </div>

      <section className="section-card">
        <div className="section-card__head">
          <div>
            <h2>How organizations work</h2>
            <p>The short version.</p>
          </div>
          <Building2 size={17} />
        </div>
        <div className="section-card__body stack">
          {[
            "An organization is a company account. Its data stays separate from every other organization.",
            "An application is what your organizations sign in to. One organization can use several applications.",
            "Each application gives that organization one workspace with its own users, roles and plan.",
          ].map((line) => (
            <div className="alert-row" key={line}>
              <div className="alert-row__meta">
                <span>{line}</span>
              </div>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
