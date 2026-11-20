"use client";

import Link from "next/link";
import { useState } from "react";

import { Alert, Button, FormField, Input } from "@adem.rejeb/ui";

interface ResetResponse {
  message?: string;
  dev_token?: string | null;
  error?: { message?: string };
}

export function ForgotPasswordForm() {
  const [email, setEmail] = useState("");
  const [status, setStatus] = useState<"idle" | "sent" | "error">("idle");
  const [message, setMessage] = useState("");
  const [devToken, setDevToken] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setStatus("idle");
    try {
      const response = await fetch("/api/proxy/v1/auth/password-reset/request", {
        method: "POST",
        headers: { "content-type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ email: email.trim() }),
      });
      const payload = (await response.json().catch(() => ({}))) as ResetResponse;
      if (!response.ok) {
        setStatus("error");
        setMessage(payload.error?.message ?? "The request could not be completed.");
        return;
      }
      setStatus("sent");
      setMessage(payload.message ?? "Check your inbox for the reset link.");
      setDevToken(payload.dev_token ?? null);
    } catch {
      setStatus("error");
      setMessage("Cannot reach the server right now. Try again in a moment.");
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="ui-card" style={{ padding: 28 }}>
      <h2 style={{ marginTop: 0 }}>Request a reset link</h2>
      {status !== "idle" ? (
        <div style={{ marginBottom: 16 }}>
          <Alert variant={status === "sent" ? "success" : "danger"}>{message}</Alert>
          {devToken ? (
            <p className="small muted">
              Development link:{" "}
              <Link href={`/reset-password?token=${encodeURIComponent(devToken)}`}>
                choose a new password
              </Link>
            </p>
          ) : null}
        </div>
      ) : null}
      <form className="field-stack" onSubmit={submit}>
        <FormField label="Email" required>
          <Input
            type="email"
            autoComplete="username"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            disabled={pending}
            required
          />
        </FormField>
        <Button type="submit" loading={pending} fullWidth>
          Send reset link
        </Button>
        <p className="small muted">
          <Link href="/login">Back to sign in</Link>
        </p>
      </form>
    </div>
  );
}
