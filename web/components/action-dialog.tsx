"use client";

import { useActionState, useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, Copy } from "lucide-react";

import { Alert, Button, Modal } from "@adem.rejeb/ui";

import type { ActionResult } from "@/app/actions";

type Action = (state: ActionResult | null, formData: FormData) => Promise<ActionResult>;

export function ActionDialog({
  label,
  title,
  description,
  action,
  children,
  submitLabel = "Save",
  triggerVariant = "primary",
  triggerSize = "md",
  size = "md",
  closeOnSuccess = true,
  destructive = false,
  icon,
}: {
  label: React.ReactNode;
  title: React.ReactNode;
  description?: React.ReactNode;
  action: Action;
  children: React.ReactNode;
  submitLabel?: string;
  triggerVariant?: "primary" | "secondary" | "outline" | "ghost" | "danger" | "link";
  triggerSize?: "sm" | "md" | "lg";
  size?: "sm" | "md" | "lg" | "xl";
  closeOnSuccess?: boolean;
  destructive?: boolean;
  icon?: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const [seq, setSeq] = useState(0);

  useEffect(() => {
    if (open) setSeq((value) => value + 1);
  }, [open]);

  return (
    <Modal open={open} onOpenChange={setOpen}>
      <Button
        variant={triggerVariant}
        size={triggerSize}
        startIcon={icon}
        onClick={() => setOpen(true)}
      >
        {label}
      </Button>
      <Modal.Content size={size}>
        {open ? (
          <DialogBody
            key={seq}
            title={title}
            description={description}
            action={action}
            submitLabel={submitLabel}
            closeOnSuccess={closeOnSuccess}
            destructive={destructive}
            onDone={() => setOpen(false)}
          >
            {children}
          </DialogBody>
        ) : null}
      </Modal.Content>
    </Modal>
  );
}

function DialogBody({
  title,
  description,
  action,
  children,
  submitLabel,
  closeOnSuccess,
  destructive,
  onDone,
}: {
  title: React.ReactNode;
  description?: React.ReactNode;
  action: Action;
  children: React.ReactNode;
  submitLabel: string;
  closeOnSuccess: boolean;
  destructive: boolean;
  onDone: () => void;
}) {
  const [state, formAction, pending] = useActionState(action, null);

  useEffect(() => {
    if (state?.ok && closeOnSuccess) onDone();
  }, [state, closeOnSuccess, onDone]);

  if (state?.ok && !closeOnSuccess) {
    return (
      <>
        <Modal.Header>
          <Modal.Title>{title}</Modal.Title>
          {description ? <Modal.Description>{description}</Modal.Description> : null}
        </Modal.Header>
        <Modal.Body>
          <div className="stack">
            <Alert variant="success" title="Done">
              {state.message ?? "The operation completed successfully."}
            </Alert>
            {state.data ? <SecretBlock data={state.data} /> : null}
          </div>
        </Modal.Body>
        <Modal.Footer>
          <Button onClick={onDone}>Close</Button>
        </Modal.Footer>
      </>
    );
  }

  return (
    <form action={formAction} className="dialog-form">
      <Modal.Header>
        <Modal.Title>{title}</Modal.Title>
        {description ? <Modal.Description>{description}</Modal.Description> : null}
      </Modal.Header>
      <Modal.Body>
        <div className="stack">
          {state && !state.ok ? (
            <Alert variant="danger" title="Could not complete" icon={<AlertTriangle size={16} />}>
              {state.message}
            </Alert>
          ) : null}
          {children}
        </div>
      </Modal.Body>
      <Modal.Footer>
        <Button type="button" variant="ghost" onClick={onDone} disabled={pending}>
          Cancel
        </Button>
        <Button
          type="submit"
          variant={destructive ? "danger" : "primary"}
          loading={pending}
          loadingText="Working…"
        >
          {submitLabel}
        </Button>
      </Modal.Footer>
    </form>
  );
}

export function SecretBlock({ data }: { data: Record<string, string> }) {
  const [copied, setCopied] = useState<string | null>(null);

  async function copy(value: string, key: string) {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(key);
    } catch {
      setCopied(null);
    }
  }

  return (
    <div className="secret-reveal">
      {Object.entries(data).map(([key, value]) => (
        <div key={key}>
          <div className="row-between">
            <span className="small muted mono">{key.replace(/_/g, " ")}</span>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              startIcon={<Copy size={13} />}
              onClick={() => copy(value, key)}
            >
              {copied === key ? "Copied" : "Copy"}
            </Button>
          </div>
          <code className="code-surface" style={{ marginTop: 6, display: "block" }}>
            {value}
          </code>
        </div>
      ))}
      <p className="small muted" style={{ margin: 0 }}>
        <CheckCircle2 size={13} style={{ verticalAlign: "-2px" }} /> Store this value in your
        secret manager. The API only returns it once.
      </p>
    </div>
  );
}
