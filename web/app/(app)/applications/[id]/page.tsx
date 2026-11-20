import Link from "next/link";
import { notFound } from "next/navigation";
import {
  Archive,
  ArrowRight,
  Check,
  Pencil,
  Play,
  Plus,
  Power,
  SlidersHorizontal,
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
  createEnvironmentAction,
  createFlagAction,
  publishApplicationConfigAction,
  setApplicationStatusAction,
  updateApplicationAction,
  updateFlagAction,
} from "@/app/actions";
import { ActionDialog } from "@/components/action-dialog";
import { EmptyState } from "@/components/pagination";
import { ProductHeader } from "@/components/product-header";
import { QuickActionForm } from "@/components/quick-action-form";
import { StatusBadge } from "@/components/status-badge";
import { apiFetch, ApiError } from "@/lib/api";
import { date, dateTime, number, titleCase } from "@/lib/format";
import type { ApplicationDetail } from "@/lib/types";

export const metadata = { title: "Application" };

type Params = Promise<{ id: string }>;

export default async function ApplicationOverviewPage({ params }: { params: Params }) {
  const { id } = await params;

  let application: ApplicationDetail;
  try {
    application = await apiFetch<ApplicationDetail>(`/v1/applications/${id}`);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) notFound();
    throw error;
  }

  const activeCredentials = application.credentials.filter(
    (credential) => credential.status === "active" && !credential.revoked_at,
  );
  const activeClients = (application.clients ?? []).filter((client) => client.status === "active");
  const canActivate = application.status === "draft" || application.status === "inactive";
  const latestConfig = application.config_history[0];

  const setup = [
    {
      done: Boolean(application.base_url),
      label: "Base URL added",
      hint: "The public address of the application.",
      href: `/applications/${application.id}/integration`,
    },
    {
      done: application.environments.length > 0,
      label: "Environment added",
      hint: "Where the application runs and is checked.",
      href: `/applications/${application.id}`,
    },
    {
      done: activeClients.length > 0,
      label: "Sign-in set up",
      hint: "Required before people can sign in.",
      href: `/applications/${application.id}/integration`,
    },
    {
      done: activeCredentials.length > 0,
      label: "API access created",
      hint: "Lets your backend read workspaces and settings.",
      href: `/applications/${application.id}/integration`,
    },
    {
      done: latestConfig !== undefined,
      label: "Settings published",
      hint: "Versioned values your application reads.",
      href: `/applications/${application.id}`,
    },
  ];
  const ready = setup.filter((item) => item.done).length;

  return (
    <div className="page-stack">
      <ProductHeader application={application} active="overview" />

      <section className="metric-grid metric-grid--5">
        <div className="section-card">
          <div className="section-card__body">
            <div className="kv-label">Workspaces</div>
            <div className="stat-inline">
              <strong>{number(application.counts.tenants ?? 0)}</strong>
              <span>{number(application.counts.active ?? 0)} active</span>
            </div>
          </div>
        </div>
        <div className="section-card">
          <div className="section-card__body">
            <div className="kv-label">Environments</div>
            <div className="stat-inline">
              <strong>{number(application.environments.length)}</strong>
              <span>configured</span>
            </div>
          </div>
        </div>
        <div className="section-card">
          <div className="section-card__body">
            <div className="kv-label">Sign-in clients</div>
            <div className="stat-inline">
              <strong>{number(activeClients.length)}</strong>
              <span>active</span>
            </div>
          </div>
        </div>
        <div className="section-card">
          <div className="section-card__body">
            <div className="kv-label">API credentials</div>
            <div className="stat-inline">
              <strong>{number(activeCredentials.length)}</strong>
              <span>active</span>
            </div>
          </div>
        </div>
        <div className="section-card">
          <div className="section-card__body">
            <div className="kv-label">Feature flags</div>
            <div className="stat-inline">
              <strong>{number(application.flags.length)}</strong>
              <span>defined</span>
            </div>
          </div>
        </div>
      </section>

      <section className="section-card">
        <div className="section-card__head">
          <div>
            <h2>Setup progress</h2>
            <p>
              {ready} of {setup.length} steps complete. Open a step to finish it.
            </p>
          </div>
          <Link className="ui-button ui-button--outline ui-button--sm" href={`/applications/${application.id}/integration`}>
            Open setup <ArrowRight size={14} />
          </Link>
        </div>
        <div className="setup-list">
          {setup.map((item) => (
            <Link className="setup-row" href={item.href} key={item.label}>
              <span className={`setup-row__state${item.done ? " is-done" : ""}`}>
                {item.done ? <Check size={13} /> : null}
              </span>
              <span className="setup-row__copy">
                <strong>{item.label}</strong>
                <span>{item.hint}</span>
              </span>
              {item.done ? (
                <Badge variant="success" size="sm">
                  Ready
                </Badge>
              ) : (
                <Badge variant="warning" size="sm">
                  Pending
                </Badge>
              )}
              <ArrowRight size={15} className="setup-row__arrow" />
            </Link>
          ))}
        </div>
      </section>

      <section className="detail-grid">
        <div className="section-card">
          <div className="section-card__head">
            <div>
              <h2>Status</h2>
              <p>An application needs an environment and a base URL before it can be activated.</p>
            </div>
            <div className="row" style={{ gap: 8 }}>
              {canActivate ? (
                <ActionDialog
                  label="Activate"
                  triggerVariant="primary"
                  icon={<Play size={15} />}
                  title="Activate application"
                  description="The application becomes available to the organizations that use it."
                  action={setApplicationStatusAction}
                  submitLabel="Activate application"
                >
                  <input type="hidden" name="id" value={application.id} />
                  <input type="hidden" name="status" value="active" />
                  <FormField label="Reason">
                    <Input name="reason" placeholder="Ready for production" />
                  </FormField>
                </ActionDialog>
              ) : null}
              {application.status === "active" ? (
                <ActionDialog
                  label="Deactivate"
                  triggerVariant="outline"
                  icon={<Power size={15} />}
                  title="Deactivate application"
                  description="Organizations stay registered, but the application is taken out of service."
                  action={setApplicationStatusAction}
                  submitLabel="Deactivate"
                >
                  <input type="hidden" name="id" value={application.id} />
                  <input type="hidden" name="status" value="inactive" />
                  <FormField label="Reason">
                    <Input name="reason" placeholder="Planned maintenance" />
                  </FormField>
                </ActionDialog>
              ) : null}
              <ActionDialog
                label="Archive"
                triggerVariant="ghost"
                icon={<Archive size={15} />}
                title="Archive application"
                description="Archiving can be undone, but no new workspaces can be added."
                action={setApplicationStatusAction}
                submitLabel="Archive application"
                destructive
              >
                <input type="hidden" name="id" value={application.id} />
                <input type="hidden" name="status" value="archived" />
                <FormField label="Reason">
                  <Input name="reason" placeholder="Application retired" />
                </FormField>
              </ActionDialog>
            </div>
          </div>
          <div className="section-card__body">
            <div className="kv-grid">
              <div>
                <div className="kv-label">Owner</div>
                <div className="kv-value">
                  {application.owner ? (
                    <>
                      {application.owner.name}{" "}
                      <span className="muted">· {application.owner.email}</span>
                    </>
                  ) : (
                    "Unassigned"
                  )}
                </div>
              </div>
              <div>
                <div className="kv-label">Created / updated</div>
                <div className="kv-value">
                  {date(application.created_at)} · {dateTime(application.updated_at)}
                </div>
              </div>
              <div>
                <div className="kv-label">Organization</div>
                <div className="kv-value">
                  {application.organization_name && application.organization_id ? (
                    <Link href={`/organizations/${application.organization_id}`}>
                      {application.organization_name}
                    </Link>
                  ) : (
                    "—"
                  )}
                </div>
              </div>
              <div>
                <div className="kv-label">Data separation</div>
                <div className="kv-value">{titleCase(application.isolation_strategy)}</div>
              </div>
            </div>
          </div>
        </div>

        <div className="section-card">
          <div className="section-card__head">
            <div>
              <h2>Application details</h2>
              <p>Basic information and default sign-in settings.</p>
            </div>
            <ActionDialog
              label="Edit"
              triggerVariant="secondary"
              icon={<Pencil size={15} />}
              title="Edit application"
              description="Changes are recorded in the activity log. The short name cannot be changed."
              action={updateApplicationAction}
              submitLabel="Save changes"
            >
              <input type="hidden" name="id" value={application.id} />
              <FormField label="Name" required>
                <Input name="name" defaultValue={application.name} required />
              </FormField>
              <FormField label="Base URL">
                <Input name="base_url" type="url" defaultValue={application.base_url ?? ""} />
              </FormField>
              <FormField label="Description">
                <Textarea name="description" rows={3} defaultValue={application.description ?? ""} />
              </FormField>
            </ActionDialog>
          </div>
          <div className="section-card__body">
            <dl className="meta-list">
              <div>
                <dt>Short name</dt>
                <dd className="mono">{application.slug}</dd>
              </div>
              <div>
                <dt>Base URL</dt>
                <dd className="mono">{application.base_url ?? "—"}</dd>
              </div>
            </dl>
          </div>
        </div>
      </section>

      <section className="section-card">
        <div className="section-card__head">
          <div>
            <h2>Environments</h2>
            <p>Where the application runs. Settings and health checks can differ per environment.</p>
          </div>
          <ActionDialog
            label="Add environment"
            triggerVariant="outline"
            icon={<Plus size={15} />}
            title="Add environment"
            action={createEnvironmentAction}
            submitLabel="Create environment"
          >
            <input type="hidden" name="id" value={application.id} />
            <div className="form-row">
              <FormField label="Name" required>
                <Input name="name" placeholder="Staging" required />
              </FormField>
              <FormField label="Slug" required>
                <Input name="slug" placeholder="staging" pattern="[a-z0-9-]+" required />
              </FormField>
            </div>
            <div className="form-row">
              <FormField label="Kind">
                <Select name="kind" defaultValue="staging">
                  <option value="development">Development</option>
                  <option value="staging">Staging</option>
                  <option value="production">Production</option>
                  <option value="custom">Custom</option>
                </Select>
              </FormField>
              <FormField label="Base URL">
                <Input name="base_url" type="url" placeholder="https://staging.example.com" />
              </FormField>
            </div>
            <FormField label="API base URL">
              <Input name="api_base_url" type="url" placeholder="https://staging.example.com/api" />
            </FormField>
          </ActionDialog>
        </div>
        {application.environments.length === 0 ? (
          <EmptyState
            title="No environments"
            description="Add a production environment before activating this application."
          />
        ) : (
          <div className="data-table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Environment</th>
                  <th>Kind</th>
                  <th>Status</th>
                  <th>Base URL</th>
                  <th>Health</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>
                {application.environments.map((environment) => (
                  <tr key={environment.id}>
                    <td>
                      <span className="cell-primary">{environment.name}</span>
                      <span className="cell-sub mono">{environment.slug}</span>
                    </td>
                    <td>{titleCase(environment.kind)}</td>
                    <td>
                      <StatusBadge status={environment.status} />
                      {environment.is_default ? <span className="cell-sub">Default</span> : null}
                    </td>
                    <td className="mono">{environment.base_url ?? "—"}</td>
                    <td>
                      {environment.health_latency_ms !== null
                        ? `${environment.health_latency_ms} ms`
                        : "—"}
                      <span className="cell-sub">{dateTime(environment.last_health_check_at)}</span>
                    </td>
                    <td className="muted">{date(environment.created_at)}</td>
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
              <h2>Settings versions</h2>
              <p>Each publish creates a new version. Environment values override the application defaults.</p>
            </div>
            <ActionDialog
              label="Publish settings"
              triggerVariant="outline"
              icon={<SlidersHorizontal size={15} />}
              title="Publish settings"
              description="Publishing creates a new version. Leave the environment blank to change the application defaults."
              action={publishApplicationConfigAction}
              submitLabel="Publish version"
              size="lg"
            >
              <input type="hidden" name="id" value={application.id} />
              <FormField label="Environment">
                <Select name="environment_id" defaultValue="">
                  <option value="">All environments</option>
                  {application.environments.map((environment) => (
                    <option key={environment.id} value={environment.id}>
                      {environment.name}
                    </option>
                  ))}
                </Select>
              </FormField>
              <FormField label="Configuration JSON" required>
                <Textarea
                  name="config"
                  rows={8}
                  defaultValue={JSON.stringify(application.config_history[0]?.config ?? {}, null, 2)}
                />
              </FormField>
              <FormField label="Change note">
                <Input name="note" placeholder="Why this configuration changed" />
              </FormField>
            </ActionDialog>
          </div>
          {application.config_history.length === 0 ? (
            <EmptyState
              title="No settings published yet"
              description="Publish the first version to set the application defaults."
            />
          ) : (
            <div className="section-card__body stack">
              {latestConfig ? (
                <div>
                  <div className="row-between" style={{ marginBottom: 8 }}>
                    <strong className="small">Latest · version {latestConfig.version}</strong>
                    <span className="small muted">{dateTime(latestConfig.created_at)}</span>
                  </div>
                  <pre className="code-block">{JSON.stringify(latestConfig.config, null, 2)}</pre>
                  {latestConfig.note ? (
                    <p className="small muted" style={{ marginBottom: 0 }}>
                      {latestConfig.note}
                    </p>
                  ) : null}
                </div>
              ) : null}
              {application.config_history.slice(1, 6).map((version) => (
                <div key={version.id} className="alert-row">
                  <div className="alert-row__meta">
                    <strong>Version {version.version}</strong>
                    <span>
                      {dateTime(version.created_at)} · {version.note ?? "No change note"}
                    </span>
                  </div>
                  <Badge variant="outline" size="sm">
                    {Object.keys(version.config).length} keys
                  </Badge>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="section-card">
          <div className="section-card__head">
            <div>
              <h2>Feature flags</h2>
              <p>Turn features on or off without shipping a new release.</p>
            </div>
            <ActionDialog
              label="Add flag"
              triggerVariant="outline"
              icon={<Plus size={15} />}
              title="Create feature flag"
              action={createFlagAction}
              submitLabel="Create flag"
            >
              <input type="hidden" name="id" value={application.id} />
              <FormField label="Key" required>
                <Input name="key" placeholder="new_checkout" pattern="[a-z0-9_-]+" required />
              </FormField>
              <FormField label="Display name" required>
                <Input name="name" placeholder="New checkout" required />
              </FormField>
              <FormField label="Description">
                <Input name="description" />
              </FormField>
              <div className="form-row">
                <FormField label="Rollout %">
                  <Input
                    name="rollout_percentage"
                    type="number"
                    min={0}
                    max={100}
                    defaultValue={100}
                  />
                </FormField>
                <FormField label="Enabled">
                  <Checkbox name="enabled" label="Enabled at creation" />
                </FormField>
              </div>
            </ActionDialog>
          </div>
          {application.flags.length === 0 ? (
            <EmptyState
              title="No flags"
              description="Flags let you roll out capabilities gradually without shipping a new build."
            />
          ) : (
            <div className="section-card__body stack">
              {application.flags.map((flag) => (
                <div key={flag.id} className="alert-row">
                  <div className="alert-row__meta">
                    <strong>
                      {flag.name} <span className="tag-mono">{flag.key}</span>
                    </strong>
                    <span>
                      {flag.description ?? "No description"} · rollout {flag.rollout_percentage}%
                    </span>
                  </div>
                  <div className="row" style={{ gap: 8 }}>
                    <StatusBadge status={flag.enabled ? "enabled" : "inactive"} />
                    <QuickActionForm
                      action={updateFlagAction}
                      fields={{
                        id: application.id,
                        flag_id: flag.id,
                        enabled: flag.enabled ? "false" : "true",
                        rollout_percentage: String(flag.rollout_percentage),
                      }}
                      label={flag.enabled ? "Disable" : "Enable"}
                      variant={flag.enabled ? "ghost" : "secondary"}
                    />
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
