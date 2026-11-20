import { Badge } from "@adem.rejeb/ui";

import { statusTone, titleCase } from "@/lib/format";

const VARIANT = {
  neutral: "neutral",
  success: "success",
  warning: "warning",
  danger: "danger",
  info: "info",
  primary: "primary",
} as const;

const STATUS_LABELS: Record<string, string> = {
  firing: "Active",
  trialing: "Trial",
  past_due: "Past due",
  provisioning: "Setting up",
};

export function StatusBadge({ status }: { status: string | null | undefined }) {
  const value = status ?? "unknown";
  return (
    <Badge variant={VARIANT[statusTone(value)]} size="sm" dot>
      {STATUS_LABELS[value.toLowerCase()] ?? titleCase(value)}
    </Badge>
  );
}

export function OutcomeBadge({ outcome }: { outcome: string }) {
  const variant = outcome === "success" ? "success" : outcome === "denied" ? "warning" : "danger";
  return (
    <Badge variant={variant} size="sm">
      {titleCase(outcome)}
    </Badge>
  );
}
