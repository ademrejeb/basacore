"use client";

import Link from "next/link";
import { useState } from "react";

import { Alert, Button, FormField, Input } from "@adem.rejeb/ui";

interface ErrorBody {
  error?: { message?: string };
}

export function AcceptInviteForm({ token }: { token: string }) {
  const [fullName, setFullName] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (password !== confirm) {
      setError("The two passwords do not match.");
      return;
    }
    setPending(true);
    setError(null);
    try {
      const response = await fetch("/api/proxy/v1/auth/accept-invite", {
        method: "POST",
        headers: { "content-type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          token,
          new_password: password,
          full_name: fullName.trim() || undefined,
        }),
      });
      if (!response.ok) {
        const payload = (await response.json().catch(() => ({}))) as ErrorBody;
        setError(payload.error?.message ?? "The invitation is invalid or expired.");
        return;
      }
      window.location.assign("/dashboard");
    } catch {
      setError("Cannot reach the server right now. Try again in a moment.");
    } finally {
      setPending(false);
    }
  }

  if (!token) {
    return (
      <Alert variant="danger" title="Missing invitation token">
        Open the link from your invitation email again, or ask an administrator to resend it.
      </Alert>
    );
  }

  return (
    <div className="ui-card" style={{ padding: 28 }}>
      <h2 style={{ marginTop: 0 }}>Create your account</h2>
      {error ? (
        <div style={{ marginBottom: 16 }}>
          <Alert variant="danger">{error}</Alert>
        </div>
      ) : null}
      <form className="field-stack" onSubmit={submit}>
        <FormField label="Full name">
          <Input
            autoComplete="name"
            value={fullName}
            onChange={(event) => setFullName(event.target.value)}
            disabled={pending}
          />
        </FormField>
        <FormField label="Password" required description="At least 12 characters.">
          <Input
            type="password"
            autoComplete="new-password"
            minLength={12}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            disabled={pending}
            required
          />
        </FormField>
        <FormField label="Confirm password" required>
          <Input
            type="password"
            autoComplete="new-password"
            minLength={12}
            value={confirm}
            onChange={(event) => setConfirm(event.target.value)}
            disabled={pending}
            required
          />
        </FormField>
        <Button type="submit" loading={pending} fullWidth>
          Activate account
        </Button>
      </form>
    </div>
  );
}
