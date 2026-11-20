import { Button, Input, Select } from "@adem.rejeb/ui";

import { EmptyState, Pagination } from "@/components/pagination";
import { OutcomeBadge } from "@/components/status-badge";
import { apiFetch, queryString } from "@/lib/api";
import { actionLabel, dateTime, number, resourceLabel, scopeLabel } from "@/lib/format";
import type { AuditLog, Page } from "@/lib/types";

export const metadata = { title: "Activity log" };

type SearchParams = Promise<{
  search?: string;
  action?: string;
  resource_type?: string;
  outcome?: string;
  scope_type?: string;
  scope_id?: string;
  offset?: string;
}>;

export default async function AuditPage({ searchParams }: { searchParams: SearchParams }) {
  const params = await searchParams;
  const filters = {
    search: params.search?.trim() ?? "",
    action: params.action?.trim() ?? "",
    resource_type: params.resource_type?.trim() ?? "",
    outcome: params.outcome ?? "",
    scope_type: params.scope_type ?? "",
    scope_id: params.scope_id?.trim() ?? "",
  };
  const offset = Number(params.offset ?? 0) || 0;

  const page = await apiFetch<Page<AuditLog>>(
    `/v1/audit-logs${queryString({ ...filters, limit: 25, offset })}`,
  );

  const filtered = Object.values(filters).some(Boolean);

  return (
    <div className="page-stack">
      <div className="page-heading">
        <div>
          <span className="eyebrow">History</span>
          <h1>Activity log</h1>
          <p>
            Every important change is recorded here, newest first. Records can be viewed
            but never edited or deleted.
          </p>
        </div>
      </div>

      <div className="section-card">
        <div className="section-card__head">
          <div>
            <h2>{number(page.total)} events</h2>
            <p>Newest first. Each row shows who did what, and when.</p>
          </div>
        </div>
        <div className="section-card__body">
          <form action="/audit" method="get" className="form-stack">
            <div className="filter-bar">
              <Input
                className="filter-input"
                name="search"
                defaultValue={filters.search}
                placeholder="Search action, person or record…"
                aria-label="Search audit events"
              />
              <Select name="outcome" defaultValue={filters.outcome} aria-label="Outcome">
                <option value="">Any result</option>
                <option value="success">Success</option>
                <option value="failure">Failure</option>
                <option value="denied">Denied</option>
              </Select>
              <Select name="scope_type" defaultValue={filters.scope_type} aria-label="Scope type">
                <option value="">Any scope</option>
                <option value="platform">Platform</option>
                <option value="organization">Organization</option>
                <option value="application">Application</option>
                <option value="tenant">Workspace</option>
              </Select>
              <Button type="submit" variant="secondary" size="sm">
                Apply filters
              </Button>
              {filtered ? (
                <a className="pagination__link" href="/audit">
                  Reset
                </a>
              ) : null}
            </div>
            <details>
              <summary className="small muted" style={{ cursor: "pointer" }}>
                Advanced filters
              </summary>
              <div className="filter-bar" style={{ marginTop: 10 }}>
                <Input
                  name="action"
                  defaultValue={filters.action}
                  placeholder="Action key, for example tenant.created"
                  aria-label="Action key"
                />
                <Input
                  name="resource_type"
                  defaultValue={filters.resource_type}
                  placeholder="Record type, for example tenant"
                  aria-label="Record type"
                />
                <Input
                  name="scope_id"
                  defaultValue={filters.scope_id}
                  placeholder="Scope id (workspace or application)"
                  aria-label="Scope id"
                />
              </div>
            </details>
          </form>
          {filters.scope_id ? (
            <p className="form-help" style={{ marginTop: 12 }}>
              Filtering to scope <span className="mono">{filters.scope_id}</span>. Workspace
              administrators only see events inside their own workspace.
            </p>
          ) : null}
        </div>

        {page.items.length === 0 ? (
          <EmptyState
            title="No events match"
            description="Adjust the filters or clear them to see the full history."
          />
        ) : (
          <>
            <div className="data-table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th scope="col">When</th>
                    <th scope="col">Action</th>
                    <th scope="col">Person</th>
                    <th scope="col">Record</th>
                    <th scope="col">Scope</th>
                    <th scope="col">Result</th>
                    <th scope="col" className="align-end">
                      Details
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {page.items.map((entry) => (
                    <tr key={entry.id}>
                      <td className="muted" style={{ whiteSpace: "nowrap" }}>
                        {dateTime(entry.created_at)}
                      </td>
                      <td>
                        <span className="cell-primary">{actionLabel(entry.action)}</span>
                        <span className="cell-sub mono">{entry.action}</span>
                      </td>
                      <td>
                        {entry.actor_email}
                        {entry.actor_id ? (
                          <span className="cell-sub mono">{entry.actor_id.slice(0, 8)}…</span>
                        ) : null}
                      </td>
                      <td>
                        {resourceLabel(entry.resource_type)}
                        {entry.resource_id ? (
                          <span className="cell-sub mono">{entry.resource_id.slice(0, 8)}…</span>
                        ) : null}
                      </td>
                      <td>
                        {scopeLabel(entry.scope_type)}
                        {entry.scope_id ? (
                          <span className="cell-sub mono">{entry.scope_id.slice(0, 8)}…</span>
                        ) : null}
                      </td>
                      <td>
                        <OutcomeBadge outcome={entry.outcome} />
                      </td>
                      <td className="align-end">
                        <details>
                          <summary className="small muted" style={{ cursor: "pointer" }}>
                            View
                          </summary>
                          <div style={{ marginTop: 8, textAlign: "left", minWidth: 240 }}>
                            <div className="cell-sub mono">Request {entry.request_id ?? "—"}</div>
                            <div className="cell-sub mono">IP {entry.ip_address ?? "not recorded"}</div>
                            {Object.keys(entry.metadata ?? {}).length ? (
                              <pre className="code-block" style={{ maxWidth: 320, marginTop: 6 }}>
                                {JSON.stringify(entry.metadata, null, 2)}
                              </pre>
                            ) : null}
                          </div>
                        </details>
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
              params={filters}
            />
          </>
        )}
      </div>
    </div>
  );
}
