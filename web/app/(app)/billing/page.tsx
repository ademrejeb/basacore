import Link from "next/link";
import { CircleDollarSign, Plus, Receipt, TrendingUp } from "lucide-react";

import { StatCard, Button, FormField, Input, Select, Textarea } from "@adem.rejeb/ui";

import { changeSubscriptionAction, createPlanAction } from "@/app/actions";
import { ActionDialog } from "@/components/action-dialog";
import { EmptyState, Pagination } from "@/components/pagination";
import { StatusBadge } from "@/components/status-badge";
import { apiFetch, queryString } from "@/lib/api";
import { date, featureLabel, metricLabel, money, number, statusLabel, titleCase } from "@/lib/format";
import type { BillingSummary, Invoice, Page, Plan, Subscription } from "@/lib/types";

export const metadata = { title: "Billing" };

type SearchParams = Promise<{
  subscription_search?: string;
  subscription_status?: string;
  subscription_offset?: string;
  invoice_search?: string;
  invoice_status?: string;
  invoice_offset?: string;
}>;

export default async function BillingPage({ searchParams }: { searchParams: SearchParams }) {
  const params = await searchParams;
  const subscriptionSearch = params.subscription_search?.trim() ?? "";
  const subscriptionStatus = params.subscription_status ?? "";
  const subscriptionOffset = Number(params.subscription_offset ?? 0) || 0;
  const invoiceSearch = params.invoice_search?.trim() ?? "";
  const invoiceStatus = params.invoice_status ?? "";
  const invoiceOffset = Number(params.invoice_offset ?? 0) || 0;

  const [summary, plans, subscriptions, invoices] = await Promise.all([
    apiFetch<BillingSummary>("/v1/billing/summary"),
    apiFetch<Plan[]>("/v1/billing/plans"),
    apiFetch<Page<Subscription>>(
      `/v1/billing/subscriptions${queryString({
        search: subscriptionSearch,
        app_status: subscriptionStatus,
        limit: 10,
        offset: subscriptionOffset,
      })}`,
    ),
    apiFetch<Page<Invoice>>(
      `/v1/billing/invoices${queryString({
        search: invoiceSearch,
        invoice_status: invoiceStatus,
        limit: 10,
        offset: invoiceOffset,
      })}`,
    ),
  ]);

  return (
    <div className="page-stack">
      <div className="page-heading">
        <div>
          <span className="eyebrow">Money</span>
          <h1>Billing &amp; plans</h1>
          <p>
            Plans set the limits and features for a workspace. Each workspace is on one
            plan, and invoices track what is owed.
          </p>
        </div>
        <div className="page-heading__actions">
          <ActionDialog
            label="Create plan"
            icon={<Plus size={15} />}
            title="Create plan"
            description="Plans are available to every workspace as soon as they are created."
            action={createPlanAction}
            submitLabel="Create plan"
          >
            <div className="form-row">
              <FormField label="Key" required>
                <Input name="key" placeholder="growth_plus" pattern="[a-z0-9_]+" required />
              </FormField>
              <FormField label="Name" required>
                <Input name="name" placeholder="Growth Plus" required />
              </FormField>
            </div>
            <FormField label="Description">
              <Textarea name="description" rows={2} />
            </FormField>
            <div className="form-row">
              <FormField label="Monthly price (USD)">
                <Input name="price_monthly" type="number" min={0} step="0.01" defaultValue={99} />
              </FormField>
              <FormField label="Yearly price (USD)">
                <Input name="price_yearly" type="number" min={0} step="0.01" defaultValue={990} />
              </FormField>
            </div>
            <div>
              <p className="small muted" style={{ margin: "0 0 8px" }}>
                Monthly allowances. Leave a field empty to skip that limit.
              </p>
              <div className="form-row">
                <FormField label="API calls">
                  <Input name="limit_api_calls" type="number" min={0} defaultValue={1_000_000} />
                </FormField>
                <FormField label="Seats">
                  <Input name="limit_seats" type="number" min={0} defaultValue={25} />
                </FormField>
                <FormField label="Storage (GB)">
                  <Input name="limit_storage_gb" type="number" min={0} defaultValue={100} />
                </FormField>
              </div>
            </div>
          </ActionDialog>
        </div>
      </div>

      <section className="metric-grid">
        <StatCard
          label="Monthly recurring revenue"
          value={money(summary.mrr_cents)}
          description={`${money(summary.arr_cents)} annualized`}
          icon={<TrendingUp size={18} />}
        />
        <StatCard
          label="Active subscriptions"
          value={number(summary.active_subscriptions)}
          description={`${Object.entries(summary.status_distribution)
            .map(([status, count]) => `${count} ${statusLabel(status)}`)
            .join(" · ")}`}
          icon={<CircleDollarSign size={18} />}
        />
        <StatCard
          label="Outstanding"
          value={money(summary.outstanding_cents)}
          description={`${number(summary.open_invoices)} open invoices`}
          icon={<Receipt size={18} />}
        />
        <StatCard
          label="Plans"
          value={number(plans.length)}
          description={`${plans.filter((plan) => plan.is_public).length} publicly available`}
          icon={<Plus size={18} />}
        />
      </section>

      <section className="section-card">
        <div className="section-card__head">
          <div>
            <h2>Available plans</h2>
            <p>Limits and features available to workspaces.</p>
          </div>
        </div>
        <div className="section-card__body">
          <div className="triple-grid">
            {plans.map((plan) => (
              <div className="section-card" key={plan.id}>
                <div className="section-card__body stack" style={{ gap: 12 }}>
                  <div className="row-between">
                    <div>
                      <strong>{plan.name}</strong>
                      <div className="small muted mono">{plan.key}</div>
                    </div>
                    <BadgeLike>{plan.is_public ? "Public" : "Private"}</BadgeLike>
                  </div>
                  <div className="stat-inline">
                    <strong>{money(plan.price_monthly_cents, plan.currency)}</strong>
                    <span>/ month</span>
                  </div>
                  <p className="small muted" style={{ margin: 0 }}>
                    {plan.description ?? "No description"}
                  </p>
                  <div className="pill-list">
                    {Object.entries(plan.limits).map(([key, value]) => (
                      <span className="tag-mono" key={key}>
                        {metricLabel(key)}: {number(Number(value))}
                      </span>
                    ))}
                  </div>
                  <div className="pill-list">
                    {plan.features.map((feature) => (
                      <span className="tag-mono" key={feature}>
                        {featureLabel(feature)}
                      </span>
                    ))}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      <div className="section-card">
        <div className="section-card__head section-card__head--stack">
          <div>
            <h2>Subscriptions</h2>
            <p>{number(subscriptions.total)} subscriptions.</p>
          </div>
          <form action="/billing" method="get" className="filter-bar">
            <Input
              className="filter-input"
              name="subscription_search"
              defaultValue={subscriptionSearch}
              placeholder="Search workspace or application…"
              aria-label="Search subscriptions"
            />
            <Select name="subscription_status" defaultValue={subscriptionStatus} aria-label="Subscription status">
              <option value="">All statuses</option>
              <option value="active">Active</option>
              <option value="trialing">Trial</option>
              <option value="past_due">Past due</option>
              <option value="paused">Paused</option>
              <option value="canceled">Cancelled</option>
            </Select>
            <Button type="submit" variant="secondary" size="sm">
              Filter
            </Button>
          </form>
        </div>

        {subscriptions.items.length === 0 ? (
          <EmptyState
            title="No subscriptions"
            description="Add a workspace with a plan to create a subscription."
          />
        ) : (
          <>
            <div className="data-table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th scope="col">Workspace</th>
                    <th scope="col">Application</th>
                    <th scope="col">Plan</th>
                    <th scope="col">Status</th>
                    <th scope="col">MRR</th>
                    <th scope="col">Period end</th>
                    <th scope="col" className="align-end">
                      Manage
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {subscriptions.items.map((subscription) => (
                    <tr key={subscription.id}>
                      <td>
                        <span className="cell-primary">
                          <Link href={`/tenants/${subscription.tenant_id}`}>
                            {subscription.tenant_name}
                          </Link>
                        </span>
                        <span className="cell-sub mono">{subscription.tenant_slug}</span>
                      </td>
                      <td>{subscription.application_name}</td>
                      <td>
                        {subscription.plan_name}
                        <span className="cell-sub">{titleCase(subscription.billing_interval)}</span>
                      </td>
                      <td>
                        <StatusBadge status={subscription.status} />
                      </td>
                      <td>{money(subscription.mrr_cents)}</td>
                      <td className="muted">{date(subscription.current_period_end)}</td>
                      <td className="align-end">
                        <ActionDialog
                          label="Change"
                          triggerVariant="outline"
                          triggerSize="sm"
                          title={`Change ${subscription.tenant_name} subscription`}
                          description="Changing the plan updates the limits immediately."
                          action={changeSubscriptionAction}
                          submitLabel="Update subscription"
                        >
                          <input type="hidden" name="id" value={subscription.id} />
                          <div className="form-row">
                            <FormField label="Plan">
                              <Select name="plan_id" defaultValue={subscription.plan_id}>
                                {plans.map((plan) => (
                                  <option key={plan.id} value={plan.id}>
                                    {plan.name} · {money(plan.price_monthly_cents)}/mo
                                  </option>
                                ))}
                              </Select>
                            </FormField>
                            <FormField label="Interval">
                              <Select name="billing_interval" defaultValue={subscription.billing_interval}>
                                <option value="monthly">Monthly</option>
                                <option value="yearly">Yearly</option>
                              </Select>
                            </FormField>
                          </div>
                          <div className="form-row">
                            <FormField label="Status">
                              <Select name="status" defaultValue={subscription.status}>
                                <option value="active">Active</option>
                                <option value="trialing">Trial</option>
                                <option value="past_due">Past due</option>
                                <option value="paused">Paused</option>
                                <option value="canceled">Cancelled</option>
                              </Select>
                            </FormField>
                            <FormField label="Discount %">
                              <Input
                                name="discount_percent"
                                type="number"
                                min={0}
                                max={100}
                                defaultValue={subscription.discount_percent}
                              />
                            </FormField>
                          </div>
                          <FormField label="Billing">
                            <label className="row small">
                              <input
                                type="checkbox"
                                name="cancel_at_period_end"
                                defaultChecked={subscription.cancel_at_period_end}
                              />{" "}
                              Cancel at period end
                            </label>
                          </FormField>
                          <FormField label="Reason">
                            <Input name="reason" placeholder="Plan upgrade" />
                          </FormField>
                        </ActionDialog>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pagination
              total={subscriptions.total}
              limit={subscriptions.limit}
              offset={subscriptions.offset}
              params={{
                subscription_search: subscriptionSearch,
                subscription_status: subscriptionStatus,
                invoice_search: invoiceSearch,
                invoice_status: invoiceStatus,
              }}
            />
          </>
        )}
      </div>

      <div className="section-card">
        <div className="section-card__head section-card__head--stack">
          <div>
            <h2>Invoices</h2>
            <p>{number(invoices.total)} invoices issued.</p>
          </div>
          <form action="/billing" method="get" className="filter-bar">
            <input type="hidden" name="subscription_search" value={subscriptionSearch} />
            <input type="hidden" name="subscription_status" value={subscriptionStatus} />
            <Input
              className="filter-input"
              name="invoice_search"
              defaultValue={invoiceSearch}
              placeholder="Search invoice or workspace…"
              aria-label="Search invoices"
            />
            <Select name="invoice_status" defaultValue={invoiceStatus} aria-label="Invoice status">
              <option value="">All statuses</option>
              <option value="draft">Draft</option>
              <option value="open">Open</option>
              <option value="paid">Paid</option>
              <option value="void">Void</option>
              <option value="uncollectible">Uncollectible</option>
            </Select>
            <Button type="submit" variant="secondary" size="sm">
              Filter
            </Button>
          </form>
        </div>

        {invoices.items.length === 0 ? (
          <EmptyState
            title="No invoices"
            description="Invoices are generated from active subscriptions and their billing interval."
          />
        ) : (
          <>
            <div className="data-table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th scope="col">Invoice</th>
                    <th scope="col">Workspace</th>
                    <th scope="col">Status</th>
                    <th scope="col">Amount</th>
                    <th scope="col">Issued</th>
                    <th scope="col">Due</th>
                    <th scope="col">Paid</th>
                  </tr>
                </thead>
                <tbody>
                  {invoices.items.map((invoice) => (
                    <tr key={invoice.id}>
                      <td>
                        <span className="cell-primary mono">{invoice.number}</span>
                      </td>
                      <td>
                        <Link href={`/tenants/${invoice.tenant_id}`}>{invoice.tenant_name}</Link>
                      </td>
                      <td>
                        <StatusBadge status={invoice.status} />
                      </td>
                      <td>{money(invoice.amount_cents, invoice.currency)}</td>
                      <td className="muted">{date(invoice.issued_at)}</td>
                      <td className="muted">{date(invoice.due_at)}</td>
                      <td className="muted">{invoice.paid_at ? date(invoice.paid_at) : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pagination
              total={invoices.total}
              limit={invoices.limit}
              offset={invoices.offset}
              params={{
                subscription_search: subscriptionSearch,
                subscription_status: subscriptionStatus,
                invoice_search: invoiceSearch,
                invoice_status: invoiceStatus,
              }}
            />
          </>
        )}
      </div>
    </div>
  );
}

function BadgeLike({ children }: { children: React.ReactNode }) {
  return <span className="tag-mono">{children}</span>;
}
