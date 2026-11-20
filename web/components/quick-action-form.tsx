"use client";

import { useActionState } from "react";

import { Button } from "@adem.rejeb/ui";

import type { ActionResult } from "@/app/actions";

type Action = (state: ActionResult | null, formData: FormData) => Promise<ActionResult>;

export function QuickActionForm({
  action,
  fields,
  label,
  variant = "outline",
  size = "sm",
}: {
  action: Action;
  fields: Record<string, string>;
  label: string;
  variant?: "primary" | "secondary" | "outline" | "ghost" | "danger" | "link";
  size?: "sm" | "md" | "lg";
}) {
  const [state, formAction, pending] = useActionState(action, null);

  return (
    <form action={formAction} className="stack" style={{ gap: 6 }}>
      {Object.entries(fields).map(([key, value]) => (
        <input type="hidden" key={key} name={key} value={value} />
      ))}
      <Button type="submit" variant={variant} size={size} loading={pending}>
        {label}
      </Button>
      {state && !state.ok ? (
        <span className="small" style={{ color: "var(--ui-p-red-400)" }}>
          {state.message}
        </span>
      ) : null}
    </form>
  );
}
