import Link from "next/link";
import { Activity, BarChart3, Gauge, TriangleAlert } from "lucide-react";

import { Badge, StatCard } from "@adem.rejeb/ui";

import { EmptyState } from "@/components/pagination";
import { StatusBadge } from "@/components/status-badge";
import { apiFetch } from "@/lib/api";
import { date, metricLabel, number, percent } from "@/lib/format";
import type { UsageReport } from "@/lib/types";

export const metadata = { title: "Usage reports" };

export default async function ReportsPage() {
  const report = await apiFetch<UsageReport>("/v1/billing/usage");
  const reporting = report.tenants.filter((tenant) =>
    tenant.metrics.some((line) => line.used > 0),
  );
  const highestUtilization = report.tenants.reduce((max, tenant) => {
    const peak = Math.max(0, ...tenant.metrics.map((line) => line.percent));
    return Math.max(max, peak);
  }, 0);

  return (
    <div className="page-stack">
      <div className="page-heading">
        <div>
          <span className="eyebrow">Workspaces</span>
          <h1>Usage reports</h1>
          <p>
            How much each workspace has used this period, compared with its plan limits.
            This is the same data used to enforce limits.
          </p>
        </div>
        <div className="page-heading__actions">
          <span className="tag-mono">period {date(report.period_start)}</span>
          <Link className="ui-button ui-button--outline" href="/billing">
            Billing &amp; plans
          </Link>
        </div>
      </div>

      <section className="metric-grid">
        <StatCard
          label="Workspaces reporting"
          value={number(reporting.length)}
          description={`${number(report.tenant_count)} workspaces in scope`}
          icon={<BarChart3 size={18} />}
        />
        <StatCard
          label="Metrics tracked"
          value={number(report.metrics.length)}
          description={report.metrics.map((entry) => metricLabel(entry.metric)).join(" · ") || "No usage yet"}
          icon={<Activity size={18} />}
        />
        <StatCard
          label="Peak utilization"
          value={percent(highestUtilization)}
          description="Highest limit use across all metrics"
          icon={<Gauge size={18} />}
        />
        <StatCard
          label="Over limit"
          value={number(report.over_limit_count)}
          description="New usage is blocked until the period resets"
          icon={<TriangleAlert size={18} />}
        />
      </section>

      <section className="section-card">
        <div className="section-card__head">
          <div>
            <h2>Platform totals</h2>
            <p>Total usage recorded for the current period.</p>
          </div>
          <span className="tag-mono">since {date(report.period_start)}</span>
        </div>
        <div className="section-card__body">
          {report.metrics.length === 0 ? (
            <p className="small muted" style={{ margin: 0 }}>
              No usage has been reported this period. Applications report usage through
              the SDK <span className="mono">POST /v1/app/usage</span> endpoint.
            </p>
          ) : (
            <div className="pill-list">
              {report.metrics.map((entry) => (
                <span className="tag-mono" key={entry.metric}>
                  {metricLabel(entry.metric)}: {number(entry.total)}
                </span>
              ))}
            </div>
          )}
        </div>
      </section>

      <div className="section-card">
        <div className="section-card__head">
          <div>
            <h2>Workspace usage</h2>
            <p>Workspaces closest to their limits come first.</p>
          </div>
          <Badge variant={report.over_limit_count ? "warning" : "success"} size="sm" dot>
            {report.over_limit_count ? `${report.over_limit_count} over limit` : "Within limits"}
          </Badge>
        </div>

        {report.tenants.length === 0 ? (
          <EmptyState
            title="No workspaces in scope"
            description="Add a workspace to start collecting usage against its plan limits."
            action={
              <Link className="ui-button ui-button--primary" href="/tenants">
                Add workspace
              </Link>
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
                  <th scope="col">Utilization</th>
                  <th scope="col" className="align-end">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody>
                {report.tenants.map((tenant) => {
                  const hasUsage = tenant.metrics.some((line) => line.used > 0);
                  const overLimit = tenant.metrics.some((line) => line.over_limit);
                  return (
                    <tr key={tenant.id}>
                      <td>
                        <span className="cell-primary">
                          <Link href={`/tenants/${tenant.id}`}>{tenant.name}</Link>
                        </span>
                        <span className="cell-sub mono">{tenant.slug}</span>
                      </td>
                      <td>{tenant.application_name ?? "—"}</td>
                      <td>
                        <StatusBadge status={tenant.status} />
                      </td>
                      <td style={{ minWidth: 260 }}>
                        {tenant.metrics.length === 0 ? (
                          <span className="small muted">No usage metrics</span>
                        ) : (
                          <div className="stack" style={{ gap: 8 }}>
                            {tenant.metrics.map((line) => (
                              <div key={line.metric}>
                                <div className="row-between">
                                  <span className="small">{metricLabel(line.metric)}</span>
                                  <span className="small muted">
                                    {number(line.used)} / {line.limit === null ? "∞" : number(line.limit)}
                                  </span>
                                </div>
                                <div className="progress-track" style={{ height: 6, marginTop: 4 }}>
                                  <div
                                    className="progress-fill"
                                    style={{ width: `${Math.min(100, Math.max(hasUsage ? 2 : 0, line.percent))}%` }}
                                    data-over={String(line.over_limit)}
                                  />
                                </div>
                              </div>
                            ))}
                          </div>
                        )}
                      </td>
                      <td className="align-end">
                        {overLimit ? (
                          <Badge variant="danger" size="sm">
                            Limit reached
                          </Badge>
                        ) : (
                          <Link className="pagination__link" href={`/tenants/${tenant.id}`}>
                            Open
                          </Link>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
