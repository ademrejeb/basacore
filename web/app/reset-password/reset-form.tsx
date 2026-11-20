"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Alert, Button, FormField, Input } from "@adem.rejeb/ui";

interface ErrorBody {
  error?: { message?: string };
}

export function ResetPasswordForm({ token }: { token: string }) {
  const router = useRouter();
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
      const response = await fetch("/api/proxy/v1/auth/password-reset/confirm", {
        method: "POST",
        headers: { "content-type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ token, new_password: password }),
      });
      if (!response.ok) {
        const payload = (await response.json().catch(() => ({}))) as ErrorBody;
        setError(payload.error?.message ?? "The reset link is invalid or expired.");
        return;
      }
      router.replace("/login?reset=1");
      router.refresh();
    } catch {
      setError("Cannot reach the server right now. Try again in a moment.");
    } finally {
      setPending(false);
    }
  }

  if (!token) {
    return (
      <Alert variant="danger" title="Missing reset token">
        Open the link from your email again, or <Link href="/forgot-password">request a new one</Link>.
      </Alert>
    );
  }

  return (
    <div className="ui-card" style={{ padding: 28 }}>
      <h2 style={{ marginTop: 0 }}>Set a new password</h2>
      {error ? (
        <div style={{ marginBottom: 16 }}>
          <Alert variant="danger">{error}</Alert>
        </div>
      ) : null}
      <form className="field-stack" onSubmit={submit}>
        <FormField label="New password" required>
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
          Save password
        </Button>
      </form>
    </div>
  );
}
