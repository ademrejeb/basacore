import Link from "next/link";
import { Activity, AlertTriangle, CheckCircle2, Radio, ServerCog } from "lucide-react";

import { Badge, Button, FormField, Input, Select, StatCard, Textarea } from "@adem.rejeb/ui";

import { resolveAlertAction, updateIncidentAction } from "@/app/actions";
import { ActionDialog } from "@/components/action-dialog";
import { EmptyState, Pagination } from "@/components/pagination";
import { QuickActionForm } from "@/components/quick-action-form";
import { StatusBadge } from "@/components/status-badge";
import { apiFetch, queryString } from "@/lib/api";
import { date, dateTime, money, number, relativeTime, titleCase } from "@/lib/format";
import type { Alert, Incident, InfrastructureSummary, Page } from "@/lib/types";

export const metadata = { title: "System health" };

interface Resource {
  id: string;
  name: string;
  resource_type: string;
  provider: string;
  region: string;
  status: string;
  application_id: string | null;
  environment_id: string | null;
  monthly_cost_cents: number;
  metadata: Record<string, unknown>;
  created_at: string;
}

interface Deployment {
  id: string;
  application_id: string;
  application_name: string;
  environment_id: string;
  environment_name: string;
  version: string;
  commit_sha: string;
  status: string;
  deployed_by: string;
  deployed_at: string;
  duration_seconds: number | null;
}

type SearchParams = Promise<{
  resource_search?: string;
  resource_type?: string;
  resource_status?: string;
  resource_offset?: string;
  incident_search?: string;
  incident_status?: string;
  incident_severity?: string;
  incident_offset?: string;
}>;

export default async function OperationsPage({ searchParams }: { searchParams: SearchParams }) {
  const params = await searchParams;
  const resourceSearch = params.resource_search?.trim() ?? "";
  const resourceType = params.resource_type ?? "";
  const resourceStatus = params.resource_status ?? "";
  const resourceOffset = Number(params.resource_offset ?? 0) || 0;
  const incidentSearch = params.incident_search?.trim() ?? "";
  const incidentStatus = params.incident_status ?? "";
  const incidentSeverity = params.incident_severity ?? "";
  const incidentOffset = Number(params.incident_offset ?? 0) || 0;

  const [summary, resources, incidents, alerts, deployments] = await Promise.all([
    apiFetch<InfrastructureSummary>("/v1/infrastructure/summary"),
    apiFetch<Page<Resource>>(
      `/v1/infrastructure/resources${queryString({
        search: resourceSearch,
        resource_type: resourceType,
        resource_status: resourceStatus,
        limit: 15,
        offset: resourceOffset,
      })}`,
    ),
    apiFetch<Page<Incident>>(
      `/v1/incidents${queryString({
        search: incidentSearch,
        incident_status: incidentStatus,
        severity: incidentSeverity,
        limit: 15,
        offset: incidentOffset,
      })}`,
    ),
    apiFetch<Alert[]>("/v1/monitoring/alerts?limit=50"),
    apiFetch<Page<Deployment>>("/v1/infrastructure/deployments?limit=8"),
  ]);

  const firing = alerts.filter((alert) => alert.status !== "resolved");
  const openIncidents = incidents.items.filter((incident) => incident.status !== "resolved");

  return (
    <div className="page-stack">
      <div className="page-heading">
        <div>
          <span className="eyebrow">Reliability</span>
          <h1>System health</h1>
          <p>
            Alerts, incidents and the infrastructure behind your applications, in one
            place.
          </p>
        </div>
        <div className="page-heading__actions">
          <Link className="ui-button ui-button--outline" href="/audit?scope_type=platform">
            Platform activity
          </Link>
        </div>
      </div>

      <section className="metric-grid">
        <StatCard
          label="Infrastructure resources"
          value={number(summary.total_resources)}
          description={`${Object.entries(summary.by_status)
            .map(([status, count]) => `${count} ${titleCase(status)}`)
            .join(" · ")}`}
          icon={<ServerCog size={18} />}
        />
        <StatCard
          label="Monthly run cost"
          value={money(summary.monthly_cost_cents)}
          description={`${summary.by_type.length} resource categories`}
          icon={<Activity size={18} />}
        />
        <StatCard
          label="Open incidents"
          value={number(openIncidents.length)}
          description={`${incidents.items.filter((incident) => incident.severity === "sev1").length} SEV-1 in page`}
          icon={<AlertTriangle size={18} />}
        />
        <StatCard
          label="Active alerts"
          value={number(firing.length)}
          description={`${alerts.length - firing.length} resolved`}
          icon={<Radio size={18} />}
        />
      </section>

      <section className="section-card">
        <div className="section-card__head">
          <div>
            <h2>Active alerts</h2>
            <p>Monitoring signals that need a decision.</p>
          </div>
          <Badge variant={firing.length ? "warning" : "success"} size="sm" dot>
            {firing.length ? `${firing.length} active` : "All clear"}
          </Badge>
        </div>
        <div className="section-card__body stack">
          {alerts.length === 0 ? (
            <p className="small muted" style={{ margin: 0 }}>
              No alerts recorded.
            </p>
          ) : (
            alerts.map((alert) => (
              <div className="alert-row" key={alert.id}>
                <div className="alert-row__meta">
                  <strong>{alert.name}</strong>
                  <span>
                    {alert.application_name ?? "Platform"} · {titleCase(alert.severity)} · triggered{" "}
                    {relativeTime(alert.last_triggered_at)}
                  </span>
                </div>
                <div className="row" style={{ gap: 8 }}>
                  <StatusBadge status={alert.status} />
                  {alert.status !== "resolved" ? (
                    <QuickActionForm
                      action={resolveAlertAction}
                      fields={{ alert_id: alert.id }}
                      label="Resolve"
                      variant="secondary"
                    />
                  ) : (
                    <CheckCircle2 size={16} color="var(--ui-p-green-400)" />
                  )}
                </div>
              </div>
            ))
          )}
        </div>
      </section>

      <div className="section-card">
        <div className="section-card__head section-card__head--stack">
          <div>
            <h2>Incidents</h2>
            <p>{number(incidents.total)} recorded incidents.</p>
          </div>
          <form action="/operations" method="get" className="filter-bar">
            <Input
              className="filter-input"
              name="incident_search"
              defaultValue={incidentSearch}
              placeholder="Search incidents…"
              aria-label="Search incidents"
            />
            <Select name="incident_status" defaultValue={incidentStatus} aria-label="Incident status">
              <option value="">Any status</option>
              <option value="open">Open</option>
              <option value="investigating">Investigating</option>
              <option value="monitoring">Monitoring</option>
              <option value="resolved">Resolved</option>
            </Select>
            <Select name="incident_severity" defaultValue={incidentSeverity} aria-label="Severity">
              <option value="">Any severity</option>
              <option value="sev1">SEV-1</option>
              <option value="sev2">SEV-2</option>
              <option value="sev3">SEV-3</option>
              <option value="sev4">SEV-4</option>
            </Select>
            <Button type="submit" variant="secondary" size="sm">
              Filter
            </Button>
          </form>
        </div>

        {incidents.items.length === 0 ? (
          <EmptyState
            title="No incidents found"
            description="Adjust the filters or clear them to see the full incident history."
          />
        ) : (
          <>
            <div className="data-table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Incident</th>
                    <th>Application</th>
                    <th>Severity</th>
                    <th>Status</th>
                    <th>Started</th>
                    <th>Resolved</th>
                    <th className="align-end">Manage</th>
                  </tr>
                </thead>
                <tbody>
                  {incidents.items.map((incident) => (
                    <tr key={incident.id}>
                      <td>
                        <span className="cell-primary">{incident.title}</span>
                        <span className="cell-sub">{incident.impact ?? incident.description}</span>
                      </td>
                      <td>{incident.application_name ?? "—"}</td>
                      <td>
                        <Badge
                          variant={
                            incident.severity === "sev1"
                              ? "danger"
                              : incident.severity === "sev2"
                                ? "warning"
                                : "neutral"
                          }
                          size="sm"
                        >
                          {incident.severity.toUpperCase()}
                        </Badge>
                      </td>
                      <td>
                        <StatusBadge status={incident.status} />
                      </td>
                      <td className="muted">{dateTime(incident.started_at)}</td>
                      <td className="muted">{incident.resolved_at ? dateTime(incident.resolved_at) : "—"}</td>
                      <td className="align-end">
                        <ActionDialog
                          label="Update"
                          triggerVariant="outline"
                          triggerSize="sm"
                          title={`Update “${incident.title}”`}
                          description="Incident updates are recorded with actor, timestamp and reason."
                          action={updateIncidentAction}
                          submitLabel="Save incident"
                        >
                          <input type="hidden" name="incident_id" value={incident.id} />
                          <div className="form-row">
                            <FormField label="Status">
                              <Select name="status" defaultValue={incident.status}>
                                <option value="open">Open</option>
                                <option value="investigating">Investigating</option>
                                <option value="monitoring">Monitoring</option>
                                <option value="resolved">Resolved</option>
                              </Select>
                            </FormField>
                            <FormField label="Severity">
                              <Select name="severity" defaultValue={incident.severity}>
                                <option value="sev1">SEV-1</option>
                                <option value="sev2">SEV-2</option>
                                <option value="sev3">SEV-3</option>
                                <option value="sev4">SEV-4</option>
                              </Select>
                            </FormField>
                          </div>
                          <FormField label="Customer impact">
                            <Input name="impact" defaultValue={incident.impact ?? ""} />
                          </FormField>
                          <FormField label="Postmortem">
                            <Textarea
                              name="postmortem"
                              rows={4}
                              defaultValue={incident.postmortem ?? ""}
                            />
                          </FormField>
                        </ActionDialog>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pagination
              total={incidents.total}
              limit={incidents.limit}
              offset={incidents.offset}
              params={{
                incident_search: incidentSearch,
                incident_status: incidentStatus,
                incident_severity: incidentSeverity,
                resource_search: resourceSearch,
                resource_type: resourceType,
                resource_status: resourceStatus,
              }}
            />
          </>
        )}
      </div>

      <div className="section-card">
        <div className="section-card__head section-card__head--stack">
          <div>
            <h2>Infrastructure inventory</h2>
            <p>{number(resources.total)} resources tracked across providers and regions.</p>
          </div>
          <form action="/operations" method="get" className="filter-bar">
            <input type="hidden" name="incident_search" value={incidentSearch} />
            <input type="hidden" name="incident_status" value={incidentStatus} />
            <input type="hidden" name="incident_severity" value={incidentSeverity} />
            <Input
              className="filter-input"
              name="resource_search"
              defaultValue={resourceSearch}
              placeholder="Search resources…"
              aria-label="Search resources"
            />
            <Select name="resource_type" defaultValue={resourceType} aria-label="Resource type">
              <option value="">Any type</option>
              <option value="container">Container</option>
              <option value="database">Database</option>
              <option value="server">Server</option>
              <option value="network">Network</option>
              <option value="managed_service">Managed service</option>
            </Select>
            <Select name="resource_status" defaultValue={resourceStatus} aria-label="Resource status">
              <option value="">Any status</option>
              <option value="healthy">Healthy</option>
              <option value="degraded">Degraded</option>
              <option value="down">Down</option>
              <option value="maintenance">Maintenance</option>
            </Select>
            <Button type="submit" variant="secondary" size="sm">
              Filter
            </Button>
          </form>
        </div>

        {resources.items.length === 0 ? (
          <EmptyState
            title="No resources"
            description="Infrastructure resources are recorded by the platform operator."
          />
        ) : (
          <>
            <div className="data-table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Resource</th>
                    <th>Type</th>
                    <th>Provider</th>
                    <th>Region</th>
                    <th>Status</th>
                    <th>Monthly cost</th>
                    <th>Created</th>
                  </tr>
                </thead>
                <tbody>
                  {resources.items.map((resource) => (
                    <tr key={resource.id}>
                      <td>
                        <span className="cell-primary mono">{resource.name}</span>
                        <span className="cell-sub">{resource.id.slice(0, 8)}…</span>
                      </td>
                      <td>{titleCase(resource.resource_type)}</td>
                      <td>{resource.provider.toUpperCase()}</td>
                      <td className="mono">{resource.region}</td>
                      <td>
                        <StatusBadge status={resource.status} />
                      </td>
                      <td>{money(resource.monthly_cost_cents)}</td>
                      <td className="muted">{date(resource.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pagination
              total={resources.total}
              limit={resources.limit}
              offset={resources.offset}
              params={{
                resource_search: resourceSearch,
                resource_type: resourceType,
                resource_status: resourceStatus,
                incident_search: incidentSearch,
                incident_status: incidentStatus,
                incident_severity: incidentSeverity,
              }}
            />
          </>
        )}
      </div>

      <section className="section-card">
        <div className="section-card__head">
          <div>
            <h2>Recent deployments</h2>
            <p>Deployment records scoped to application and environment.</p>
          </div>
          <span className="tag-mono">last {deployments.items.length}</span>
        </div>
        {deployments.items.length === 0 ? (
          <EmptyState
            title="No deployments"
            description="Deployment records appear once an application ships through the platform."
          />
        ) : (
          <div className="data-table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Application</th>
                  <th>Environment</th>
                  <th>Version</th>
                  <th>Commit</th>
                  <th>Status</th>
                  <th>Deployed</th>
                  <th>Duration</th>
                </tr>
              </thead>
              <tbody>
                {deployments.items.map((deployment) => (
                  <tr key={deployment.id}>
                    <td>{deployment.application_name}</td>
                    <td>{deployment.environment_name}</td>
                    <td className="mono">{deployment.version}</td>
                    <td className="mono">{deployment.commit_sha.slice(0, 8)}</td>
                    <td>
                      <StatusBadge status={deployment.status} />
                    </td>
                    <td className="muted">{dateTime(deployment.deployed_at)}</td>
                    <td className="muted">
                      {deployment.duration_seconds ? `${deployment.duration_seconds}s` : "—"}
                    </td>
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
