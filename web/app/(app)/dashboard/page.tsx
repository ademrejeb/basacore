import Link from "next/link";
import {
  Activity,
  Building2,
  CircleDollarSign,
  ShieldAlert,
  Sparkles,
  Users,
} from "lucide-react";

import { StatCard } from "@adem.rejeb/ui";

import { OutcomeBadge } from "@/components/status-badge";
import { apiFetch } from "@/lib/api";
import { actionLabel, money, number, relativeTime, resourceLabel } from "@/lib/format";
import type { AuditLog, DashboardSummary } from "@/lib/types";

export const metadata = { title: "Dashboard" };

export default async function DashboardPage() {
  const [summary, activity] = await Promise.all([
    apiFetch<DashboardSummary>("/v1/dashboard/summary"),
    apiFetch<AuditLog[]>("/v1/dashboard/activity?limit=10"),
  ]);

  return (
    <div className="page-stack">
      <div className="page-heading">
        <div>
          <span className="eyebrow">Overview</span>
          <h1>Welcome back</h1>
          <p>
            Here is what is happening across your applications, organizations and
            workspaces.
          </p>
        </div>
        <div className="page-heading__actions">
          <Link className="ui-button ui-button--outline" href="/tenants?status=trial">
            Review trials
          </Link>
          <Link className="ui-button ui-button--primary" href="/tenants">
            Add workspace
          </Link>
        </div>
      </div>

      <section className="metric-grid" aria-label="Platform totals">
        <StatCard
          label="Applications"
          value={number(summary.applications.total)}
          description={`${number(summary.applications.active)} active`}
          icon={<Sparkles size={18} />}
        />
        <StatCard
          label="Organizations"
          value={number(summary.organizations.total)}
          description={`${number(summary.organizations.active)} active`}
          icon={<Building2 size={18} />}
        />
        <StatCard
          label="Workspaces"
          value={number(summary.tenants.total)}
          description={`${number(summary.tenants.active)} active · ${number(summary.tenants.new_this_month)} new this month`}
          icon={<Users size={18} />}
        />
        <StatCard
          label="Monthly revenue"
          value={money(summary.billing.mrr_cents)}
          description={`${number(summary.billing.active_subscriptions)} subscriptions · ${money(summary.billing.outstanding_cents)} unpaid`}
          icon={<CircleDollarSign size={18} />}
        />
      </section>

      <section className="split-grid">
        <div className="section-card">
          <div className="section-card__head">
            <div>
              <h2>Workspaces by application</h2>
              <p>How many workspaces each application has.</p>
            </div>
            <Link className="small muted" href="/applications">
              View applications
            </Link>
          </div>
          <div className="section-card__body">
            {summary.application_distribution.length === 0 ? (
              <p className="small muted" style={{ margin: 0 }}>
                No applications yet. Add one to get started.
              </p>
            ) : (
              <div className="distribution-list">
                {summary.application_distribution.map((row) => (
                  <div className="distribution-list__row" key={row.name}>
                    <div>
                      <div className="row-between">
                        <strong>{row.name}</strong>
                        <span className="muted small">{number(row.tenants)}</span>
                      </div>
                      <div className="distribution-list__bar">
                        <div
                          className="distribution-list__fill"
                          style={{
                            width: `${Math.max(
                              4,
                              (row.tenants / Math.max(1, summary.tenants.total)) * 100,
                            )}%`,
                          }}
                        />
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        <div className="section-card">
          <div className="section-card__head">
            <div>
              <h2>Needs attention</h2>
              <p>Anything that may need a follow-up.</p>
            </div>
            <Link className="small muted" href="/operations">
              System health
            </Link>
          </div>
          <div className="section-card__body stack">
            <div className="alert-row">
              <div className="alert-row__meta">
                <strong>Open incidents</strong>
                <span>
                  {number(summary.incidents.open)} open · {number(summary.incidents.sev1)}{" "}
                  high priority
                </span>
              </div>
              <span className={summary.incidents.sev1 > 0 ? "status-dot" : ""}>
                <ShieldAlert size={18} />
              </span>
            </div>
            <div className="alert-row">
              <div className="alert-row__meta">
                <strong>Unpaid invoices</strong>
                <span>{money(summary.billing.outstanding_cents)} outstanding</span>
              </div>
              <Activity size={18} />
            </div>
            <Link className="alert-row" href="/tenants?status=trial">
              <div className="alert-row__meta">
                <strong>Trials in progress</strong>
                <span>{number(summary.tenants.trial)} workspaces on a trial</span>
              </div>
              <Users size={18} />
            </Link>
          </div>
        </div>
      </section>

      <section className="section-card">
        <div className="section-card__head">
          <div>
            <h2>Recent activity</h2>
            <p>The latest changes made in Basacore.</p>
          </div>
          <Link className="small muted" href="/audit">
            View all activity
          </Link>
        </div>
        {activity.length === 0 ? (
          <div className="section-card__body">
            <p className="small muted" style={{ margin: 0 }}>
              No activity has been recorded yet.
            </p>
          </div>
        ) : (
          <div className="data-table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th scope="col">Action</th>
                  <th scope="col">Person</th>
                  <th scope="col">Result</th>
                  <th scope="col">When</th>
                </tr>
              </thead>
              <tbody>
                {activity.map((entry) => (
                  <tr key={entry.id}>
                    <td>
                      <span>{actionLabel(entry.action)}</span>
                      <span className="cell-sub">
                        {resourceLabel(entry.resource_type)}
                        {entry.resource_id ? ` · ${entry.resource_id.slice(0, 8)}` : ""}
                      </span>
                    </td>
                    <td>{entry.actor_email}</td>
                    <td>
                      <OutcomeBadge outcome={entry.outcome} />
                    </td>
                    <td className="muted">{relativeTime(entry.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
