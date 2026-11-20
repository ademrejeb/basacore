"use client";

import { useState } from "react";
import { Check, Copy } from "lucide-react";

import { Button } from "@adem.rejeb/ui";

export function CopyButton({ value, label = "Copy" }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopied(false);
    }
  }

  return (
    <Button
      type="button"
      size="xs"
      variant="ghost"
      startIcon={copied ? <Check size={12} /> : <Copy size={12} />}
      onClick={copy}
      aria-label={`Copy ${label.toLowerCase()}`}
    >
      {copied ? "Copied" : label}
    </Button>
  );
}

export function CopyValue({
  label,
  value,
  description,
}: {
  label: string;
  value: string;
  description?: string;
}) {
  return (
    <div className="copy-value">
      <div className="copy-value__head">
        <span className="kv-label">{label}</span>
        <CopyButton value={value} />
      </div>
      <code className="copy-value__code">{value}</code>
      {description ? <p className="copy-value__hint">{description}</p> : null}
    </div>
  );
}
