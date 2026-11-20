"use client";

import { useActionState, useState } from "react";
import { CheckCircle2, Copy, KeyRound, ShieldCheck, Smartphone } from "lucide-react";

import { Alert, Badge, Button, FormField, Input } from "@adem.rejeb/ui";

import { confirmMfaAction, enrollMfaAction } from "@/app/actions";
import type { ActionResult } from "@/app/actions";

export function MfaSetup({ enabled }: { enabled: boolean }) {
  const [enrollState, enrollAction, enrolling] = useActionState(enrollMfaAction, null);
  const [confirmState, confirmAction, confirming] = useActionState(confirmMfaAction, null);
  const [copied, setCopied] = useState(false);

  const enrollment = enrollState?.ok ? enrollState.data : undefined;
  const recoveryCodes = confirmState?.ok ? confirmState.data?.recovery_codes : undefined;

  async function copySecret(value: string) {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  if (recoveryCodes) {
    return (
      <div className="stack">
        <Alert variant="success" title="Multi-factor authentication enabled">
          Store these recovery codes somewhere safe. Each code can be used once if you lose
          access to your authenticator. They are never shown again.
        </Alert>
        <pre className="code-block">{recoveryCodes}</pre>
        <div className="row small muted">
          <ShieldCheck size={14} /> Recovery codes are hashed at rest after this screen.
        </div>
      </div>
    );
  }

  if (enabled) {
    return (
      <div className="stack">
        <div className="alert-row">
          <div className="alert-row__meta">
            <strong>TOTP authenticator enrolled</strong>
            <span>Sign-in requires a 6-digit code or a single-use recovery code.</span>
          </div>
          <Badge variant="success" size="sm" icon={<CheckCircle2 size={12} />}>
            Active
          </Badge>
        </div>
        <p className="small muted" style={{ margin: 0 }}>
          To change devices, disable MFA below with your password and enroll again.
        </p>
      </div>
    );
  }

  if (!enrollment) {
    return (
      <div className="stack">
        <div className="alert-row">
          <div className="alert-row__meta">
            <strong>Multi-factor authentication</strong>
            <span>
              Add a second factor with any TOTP app (1Password, Authy, Google Authenticator).
            </span>
          </div>
          <Badge variant="warning" size="sm">
            Recommended
          </Badge>
        </div>
        {enrollState && !enrollState.ok ? (
          <Alert variant="danger" title="Could not start enrollment">
            {enrollState.message}
          </Alert>
        ) : null}
        <form action={enrollAction}>
          <Button type="submit" variant="primary" loading={enrolling} loadingText="Preparing…" startIcon={<KeyRound size={15} />}>
            Set up authenticator
          </Button>
        </form>
      </div>
    );
  }

  return (
    <div className="stack">
      <Alert variant="info" title="Scan or enter this secret">
        Open your authenticator app, add a new account manually, and enter the secret below.
      </Alert>
      <div>
        <div className="row-between">
          <span className="kv-label">Setup link</span>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            startIcon={<Copy size={13} />}
            onClick={() => copySecret(enrollment.provisioning_uri ?? "")}
          >
            {copied ? "Copied" : "Copy"}
          </Button>
        </div>
        <pre className="code-block" style={{ whiteSpace: "pre-wrap" }}>
          {enrollment.provisioning_uri}
        </pre>
      </div>
      <div>
        <div className="kv-label">Manual secret</div>
        <pre className="code-block">{enrollment.secret}</pre>
      </div>
      <form action={confirmAction} className="stack">
        {confirmState && !confirmState.ok ? (
          <Alert variant="danger" title="Could not verify code">
            {confirmState.message}
          </Alert>
        ) : null}
        <FormField label="Verification code" description="6 digits from your authenticator." required>
          <Input
            name="code"
            inputMode="numeric"
            pattern="[0-9]{6}"
            maxLength={6}
            minLength={6}
            placeholder="123456"
            required
            autoFocus
          />
        </FormField>
        <Button type="submit" variant="primary" loading={confirming} loadingText="Verifying…" startIcon={<Smartphone size={15} />}>
          Verify and enable
        </Button>
      </form>
    </div>
  );
}
