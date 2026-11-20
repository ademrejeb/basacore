import { KeyRound, Plus, ShieldCheck, Unlock, UserPlus } from "lucide-react";

import {
  Badge,
  Button,
  Checkbox,
  FormField,
  Input,
  Select,
} from "@adem.rejeb/ui";

import { createRoleAction, createUserAction, unlockUserAction } from "@/app/actions";
import { ActionDialog } from "@/components/action-dialog";
import { EmptyState, Pagination } from "@/components/pagination";
import { QuickActionForm } from "@/components/quick-action-form";
import { StatusBadge } from "@/components/status-badge";
import { apiFetch, queryString } from "@/lib/api";
import { date, number, titleCase } from "@/lib/format";
import type { Organization, Page, Permission, Role, UserAccount } from "@/lib/types";

export const metadata = { title: "Access" };

type SearchParams = Promise<{
  search?: string;
  status?: string;
  type?: string;
  offset?: string;
}>;

function scopeLabel(value: string): string {
  return value === "tenant" ? "Workspace" : titleCase(value);
}

export default async function AccessPage({ searchParams }: { searchParams: SearchParams }) {
  const params = await searchParams;
  const search = params.search?.trim() ?? "";
  const status = params.status ?? "";
  const type = params.type ?? "";
  const offset = Number(params.offset ?? 0) || 0;

  const [roles, permissions, users, organizations] = await Promise.all([
    apiFetch<Role[]>("/v1/access/roles"),
    apiFetch<Permission[]>("/v1/access/permissions"),
    apiFetch<Page<UserAccount>>(
      `/v1/users${queryString({ search, user_status: status, user_type: type, limit: 20, offset })}`,
    ),
    // A role that can read roles is not guaranteed to read the organization
    // registry; degrade to platform-only assignment instead of failing the page.
    apiFetch<Page<Organization>>("/v1/organizations?limit=200").catch(
      () => ({ items: [], total: 0, limit: 0, offset: 0 }) as Page<Organization>,
    ),
  ]);

  const permissionGroups = new Map<string, Permission[]>();
  for (const permission of permissions) {
    const group = permissionGroups.get(permission.category) ?? [];
    group.push(permission);
    permissionGroups.set(permission.category, group);
  }

  const assignableRoles = roles.filter((role) =>
    ["platform", "organization"].includes(role.scope_type),
  );

  return (
    <div className="page-stack">
      <div className="page-heading">
        <div>
          <span className="eyebrow">People and permissions</span>
          <h1>Access</h1>
          <p>
            Roles decide what each person can do. A role bundles permissions and can be
            assigned at the platform, organization, application or workspace level.
          </p>
        </div>
        <div className="page-heading__actions">
          <ActionDialog
            label="Create role"
            icon={<Plus size={15} />}
            title="Create a role"
            description="Custom roles add permissions. Built-in roles cannot be changed."
            action={createRoleAction}
            submitLabel="Create role"
            size="xl"
          >
            <div className="form-row">
              <FormField label="Key" required>
                <Input name="key" placeholder="billing_ops" pattern="[a-z0-9_]+" required />
              </FormField>
              <FormField label="Name" required>
                <Input name="name" placeholder="Billing Operations" required />
              </FormField>
            </div>
            <div className="form-row">
              <FormField label="Scope">
                <Select
                  name="scope_type"
                  defaultValue={organizations.items.length ? "organization" : "platform"}
                >
                  <option value="platform">Platform</option>
                  <option value="organization">Organization</option>
                </Select>
              </FormField>
              <FormField label="Organization" description="Required for organization roles.">
                <Select name="organization_id" defaultValue={organizations.items[0]?.id ?? ""}>
                  <option value="">No organization</option>
                  {organizations.items.map((organization) => (
                    <option key={organization.id} value={organization.id}>
                      {organization.name}
                    </option>
                  ))}
                </Select>
              </FormField>
            </div>
            <FormField label="Description">
              <Input name="description" />
            </FormField>
            <FormField label="Permissions">
              <div className="permission-picker">
                {[...permissionGroups.entries()].map(([category, items]) => (
                  <div key={category} className="stack" style={{ gap: 6 }}>
                    <div className="kv-label">{category}</div>
                    <div className="permission-grid">
                      {items.map((permission) => (
                        <Checkbox
                          key={permission.key}
                          name="permissions"
                          value={permission.key}
                          label={<span className="mono">{permission.key}</span>}
                        />
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </FormField>
          </ActionDialog>
          <ActionDialog
            label="Add user"
            triggerVariant="secondary"
            icon={<UserPlus size={15} />}
            title="Create a platform user"
            description="The user gets a temporary password and can change it after signing in."
            action={createUserAction}
            submitLabel="Create user"
            size="lg"
          >
            <div className="form-row">
              <FormField label="Full name" required>
                <Input name="full_name" required />
              </FormField>
              <FormField label="Email" required>
                <Input name="email" type="email" required />
              </FormField>
            </div>
            <div className="form-row">
              <FormField label="Initial password" required description="Minimum 12 characters.">
                <Input name="password" type="password" minLength={12} required />
              </FormField>
              <FormField label="User type">
                <Select name="user_type" defaultValue="internal">
                  <option value="internal">Internal</option>
                  <option value="external">External</option>
                </Select>
              </FormField>
              <FormField label="Organization" description="Required for organization roles.">
                <Select name="organization_id" defaultValue={organizations.items[0]?.id ?? ""}>
                  <option value="">No organization</option>
                  {organizations.items.map((organization) => (
                    <option key={organization.id} value={organization.id}>
                      {organization.name}
                    </option>
                  ))}
                </Select>
              </FormField>
            </div>
            <div className="form-row">
              <FormField label="Job title">
                <Input name="job_title" />
              </FormField>
              <FormField label="Department">
                <Input name="department" />
              </FormField>
            </div>
            <FormField label="Initial roles">
              <div className="permission-grid">
                {assignableRoles.map((role) => (
                  <Checkbox
                    key={role.id}
                    name="role_keys"
                    value={role.key}
                    label={`${role.name} (${scopeLabel(role.scope_type)})`}
                  />
                ))}
              </div>
            </FormField>
          </ActionDialog>
        </div>
      </div>

      <section className="section-card">
        <div className="section-card__head">
          <div>
            <h2>Roles</h2>
            <p>{number(roles.length)} roles across platform, organization, application and workspace levels.</p>
          </div>
          <Badge variant="outline" size="sm" icon={<ShieldCheck size={12} />}>
            {roles.filter((role) => role.is_system).length} system roles
          </Badge>
        </div>
        <div className="section-card__body stack">
          {roles.map((role) => (
            <details className="role-card" key={role.id}>
              <summary>
                <div className="row" style={{ gap: 10 }}>
                  <strong>{role.name}</strong>
                  <span className="tag-mono">{role.key}</span>
                  <Badge variant="outline" size="sm">
                    {scopeLabel(role.scope_type)}
                  </Badge>
                  {role.is_system ? (
                    <Badge variant="info" size="sm">
                      System
                    </Badge>
                  ) : null}
                </div>
                <span className="small muted">
                  {role.permissions.length} permissions · {number(role.assignments)} assignments
                </span>
              </summary>
              <div className="role-card__body">
                <p className="small muted" style={{ marginTop: 0 }}>
                  {role.description || "No description provided."}
                </p>
                <div className="pill-list">
                  {role.permissions.map((permission) => (
                    <span className="tag-mono" key={permission}>
                      {permission}
                    </span>
                  ))}
                </div>
              </div>
            </details>
          ))}
        </div>
      </section>

      <div className="section-card">
        <div className="section-card__head section-card__head--stack">
          <div>
            <h2>Users</h2>
            <p>{number(users.total)} accounts. Locked accounts can be unlocked here.</p>
          </div>
          <form action="/access" method="get" className="filter-bar">
            <Input
              className="filter-input"
              name="search"
              defaultValue={search}
              placeholder="Search name or email…"
              aria-label="Search users"
            />
            <Select name="status" defaultValue={status} aria-label="User status">
              <option value="">All statuses</option>
              <option value="active">Active</option>
              <option value="invited">Invited</option>
              <option value="suspended">Suspended</option>
              <option value="disabled">Disabled</option>
            </Select>
            <Select name="type" defaultValue={type} aria-label="User type">
              <option value="">All types</option>
              <option value="internal">Internal</option>
              <option value="external">External</option>
            </Select>
            <Button type="submit" variant="secondary" size="sm">
              Filter
            </Button>
          </form>
        </div>

        {users.items.length === 0 ? (
          <EmptyState
            title="No users"
            description="Create a platform user, or invite someone from a workspace."
          />
        ) : (
          <>
            <div className="data-table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>User</th>
                    <th>Type</th>
                    <th>Status</th>
                    <th>Roles</th>
                    <th>MFA</th>
                    <th>Last login</th>
                    <th>Created</th>
                    <th className="align-end">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {users.items.map((user) => (
                    <tr key={user.id}>
                      <td>
                        <span className="cell-primary">{user.full_name}</span>
                        <span className="cell-sub">{user.email}</span>
                      </td>
                      <td>{titleCase(user.user_type)}</td>
                      <td>
                        <StatusBadge status={user.status} />
                        {user.locked_until ? (
                          <span className="cell-sub">Locked until {date(user.locked_until)}</span>
                        ) : null}
                      </td>
                      <td>
                        <div className="pill-list">
                          {user.roles.length === 0 ? (
                            <span className="muted small">No roles</span>
                          ) : (
                            user.roles.map((role) => (
                              <span className="tag-mono" key={`${role.role_id}-${role.scope_type}`}>
                                {role.role_key}
                              </span>
                            ))
                          )}
                        </div>
                      </td>
                      <td>
                        {user.mfa_enabled ? (
                          <Badge variant="success" size="sm">
                            Enabled
                          </Badge>
                        ) : (
                          <span className="muted small">Off</span>
                        )}
                      </td>
                      <td className="muted">{user.last_login_at ? date(user.last_login_at) : "Never"}</td>
                      <td className="muted">{date(user.created_at)}</td>
                      <td className="align-end">
                        {user.locked_until || user.failed_login_count >= 5 ? (
                          <QuickActionForm
                            action={unlockUserAction}
                            fields={{ user_id: user.id }}
                            label="Unlock"
                            variant="outline"
                          />
                        ) : (
                          <span className="small muted">—</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pagination
              total={users.total}
              limit={users.limit}
              offset={users.offset}
              params={{ search, status, type }}
            />
          </>
        )}
      </div>

      <section className="section-card">
        <details>
          <summary className="section-card__head section-card__summary">
            <div>
              <h2>Permissions</h2>
              <p>Technical keys behind each protected action. Open only when you need the reference.</p>
            </div>
            <Badge variant="outline" size="sm" icon={<KeyRound size={12} />}>
              {permissions.length} permissions
            </Badge>
          </summary>
          <div className="section-card__body stack">
            {[...permissionGroups.entries()].map(([category, items]) => (
              <div key={category}>
                <div className="kv-label" style={{ marginBottom: 8 }}>
                  {titleCase(category)}
                </div>
                <div className="permission-grid">
                  {items.map((permission) => (
                    <div className="permission-item" key={permission.key}>
                      <span className="mono">{permission.key}</span>
                      <span className="small muted">{permission.description}</span>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </details>
      </section>
    </div>
  );
}
