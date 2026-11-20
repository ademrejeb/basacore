"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { ArrowRight, KeyRound, ShieldCheck } from "lucide-react";

import { Alert, Button, FormField, Input } from "@adem.rejeb/ui";

interface LoginResponse {
  status: "authenticated" | "mfa_required" | "mfa_enrollment_required";
  challenge_token?: string;
  recovery_codes?: string[];
  user?: { full_name: string; email: string };
}

interface EnrollmentResponse {
  secret: string;
  provisioning_uri: string;
}

interface ApiErrorBody {
  error?: { message?: string; code?: string };
}

interface LoginFormProps {
  initialEmail?: string;
  returnTo?: string | null;
}

type Stage = "credentials" | "verify" | "enroll";

export function LoginForm({ initialEmail = "", returnTo = null }: LoginFormProps) {
  const router = useRouter();
  const [email, setEmail] = useState(initialEmail);
  const [password, setPassword] = useState("");
  const [stage, setStage] = useState<Stage>("credentials");
  const [challenge, setChallenge] = useState<string | null>(null);
  const [enrollment, setEnrollment] = useState<EnrollmentResponse | null>(null);
  const [recoveryCodes, setRecoveryCodes] = useState<string[] | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  function finish() {
    if (returnTo) {
      // Full navigation: the bridge route replies with a cross-origin redirect
      // to the API authorization endpoint, which the client router cannot follow.
      window.location.assign(`/auth/continue?return_to=${encodeURIComponent(returnTo)}`);
      return;
    }
    router.replace("/dashboard");
    router.refresh();
  }

  async function post<T>(path: string, body: Record<string, unknown>): Promise<T> {
    const response = await fetch(path, {
      method: "POST",
      headers: { "content-type": "application/json" },
      credentials: "include",
      body: JSON.stringify(body),
    });
    const payload = (await response.json().catch(() => ({}))) as T & ApiErrorBody;
    if (!response.ok) {
      throw new Error(payload.error?.message ?? "That request did not work.");
    }
    return payload;
  }

  async function startEnrollment(challengeToken: string) {
    const setup = await post<EnrollmentResponse>("/api/proxy/v1/auth/mfa/setup/start", {
      challenge_token: challengeToken,
    });
    setEnrollment(setup);
    setChallenge(challengeToken);
    setStage("enroll");
  }

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setError(null);

    try {
      if (stage === "credentials") {
        const payload = await post<LoginResponse>("/api/proxy/v1/auth/login", {
          email: email.trim(),
          password,
        });
        if (payload.status === "mfa_required" && payload.challenge_token) {
          setChallenge(payload.challenge_token);
          setStage("verify");
          setCode("");
          return;
        }
        if (payload.status === "mfa_enrollment_required" && payload.challenge_token) {
          await startEnrollment(payload.challenge_token);
          return;
        }
        finish();
        return;
      }

      if (stage === "verify") {
        await post<LoginResponse>("/api/proxy/v1/auth/mfa/verify", {
          challenge_token: challenge,
          code: code.trim(),
        });
        finish();
        return;
      }

      const payload = await post<LoginResponse>("/api/proxy/v1/auth/mfa/setup/confirm", {
        challenge_token: challenge,
        code: code.trim(),
      });
      if (payload.recovery_codes?.length) {
        setRecoveryCodes(payload.recovery_codes);
        return;
      }
      finish();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Cannot reach the server right now.");
    } finally {
      setPending(false);
    }
  }

  function resetFlow() {
    setStage("credentials");
    setChallenge(null);
    setEnrollment(null);
    setRecoveryCodes(null);
    setCode("");
    setError(null);
  }

  if (recoveryCodes) {
    return (
      <div className="ui-card" style={{ padding: 28 }}>
        <Alert variant="success" title="Authenticator enabled">
          Store these single-use recovery codes somewhere safe. They are shown only once.
        </Alert>
        <pre className="code-block" style={{ marginTop: 16, whiteSpace: "pre-wrap" }}>
          {recoveryCodes.join("\n")}
        </pre>
        <Button onClick={finish} fullWidth style={{ marginTop: 16 }}>
          Continue
        </Button>
      </div>
    );
  }

  return (
    <div className="ui-card" style={{ padding: 28 }}>
      <div className="stack" style={{ gap: 8, marginBottom: 22 }}>
        <span className="eyebrow">
          {stage === "verify" ? "Second factor" : stage === "enroll" ? "Enroll MFA" : "Welcome back"}
        </span>
        <h2 style={{ margin: 0, fontSize: "1.55rem", letterSpacing: "-0.03em" }}>
          {stage === "verify"
            ? "Enter your authenticator code"
            : stage === "enroll"
              ? "Set up your authenticator"
              : "Sign in to Basacore"}
        </h2>
        <p className="muted small" style={{ margin: 0 }}>
          {stage === "verify"
            ? "Open your authenticator app and enter the six-digit code for this account."
            : stage === "enroll"
              ? "Your organization requires multi-factor authentication. Add this account to your authenticator app, then enter the code."
              : "Use your Basacore account to continue."}
        </p>
      </div>

      {error ? (
        <div style={{ marginBottom: 16 }}>
          <Alert variant="danger" title="Sign-in failed">
            {error}
          </Alert>
        </div>
      ) : null}

      {stage === "enroll" && enrollment ? (
        <div className="stack" style={{ marginBottom: 16 }}>
          <div>
            <div className="kv-label">Setup link</div>
            <pre className="code-block" style={{ whiteSpace: "pre-wrap" }}>
              {enrollment.provisioning_uri}
            </pre>
          </div>
          <div>
            <div className="kv-label">Manual secret</div>
            <pre className="code-block">{enrollment.secret}</pre>
          </div>
        </div>
      ) : null}

      <form className="field-stack" onSubmit={submit}>
        {stage === "credentials" ? (
          <>
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
            <FormField label="Password" required>
              <Input
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                placeholder="••••••••••••"
                disabled={pending}
                required
              />
            </FormField>
            <Button type="submit" loading={pending} fullWidth endIcon={<ArrowRight size={16} />}>
              <KeyRound size={16} /> Continue
            </Button>
            <div className="row-between small">
              <Link href="/forgot-password">Forgot password?</Link>
            </div>
          </>
        ) : (
          <>
            <FormField label={stage === "enroll" ? "Verification code" : "Authenticator code"} required>
              <Input
                autoFocus
                inputMode={stage === "verify" ? "text" : "numeric"}
                autoComplete="one-time-code"
                pattern="[0-9A-Za-z-]{6,32}"
                value={code}
                onChange={(event) => setCode(event.target.value)}
                placeholder="123456"
                disabled={pending}
                required
              />
            </FormField>
            <Button type="submit" loading={pending} fullWidth>
              <ShieldCheck size={16} />
              {stage === "enroll" ? "Enable and continue" : "Verify and continue"}
            </Button>
            <Button type="button" variant="link" onClick={resetFlow}>
              Use a different account
            </Button>
          </>
        )}
      </form>

      <p className="small muted" style={{ marginTop: 18, marginBottom: 0 }}>
        Your session is protected. You can review active sessions and sign out from
        Settings at any time.
      </p>
    </div>
  );
}
