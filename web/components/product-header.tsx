import Link from "next/link";

import { Badge } from "@adem.rejeb/ui";

import { ProductTabs } from "@/components/product-tabs";
import { StatusBadge } from "@/components/status-badge";
import { titleCase } from "@/lib/format";
import type { ApplicationDetail } from "@/lib/types";

export function ProductHeader({
  application,
  active,
}: {
  application: ApplicationDetail;
  active: "overview" | "integration" | "workspaces";
}) {
  const initials = application.name
    .split(/\s+/)
    .slice(0, 2)
    .map((word) => word[0]?.toUpperCase())
    .join("");

  return (
    <div className="panel-header">
      <div className="panel-header__main">
        <span className="detail-hero__mark">{initials}</span>
        <div className="panel-header__copy">
          <div className="row" style={{ gap: 10 }}>
            <h1>{application.name}</h1>
            <StatusBadge status={application.status} />
          </div>
          <p>{application.description ?? "No description has been added yet."}</p>
          <div className="panel-header__meta">
            <span className="mono">{application.slug}</span>
            {application.organization_name ? (
              <span>
                Organization:{" "}
                <Link href={`/organizations/${application.organization_id}`}>
                  {application.organization_name}
                </Link>
              </span>
            ) : null}
            <span className="mono">{application.base_url ?? "No base URL"}</span>
            <Badge variant="outline" size="sm">
              {application.isolation_strategy === "shared_database"
                ? "Shared database"
                : titleCase(application.isolation_strategy)}
            </Badge>
          </div>
        </div>
      </div>
      <ProductTabs id={application.id} active={active} />
    </div>
  );
}
