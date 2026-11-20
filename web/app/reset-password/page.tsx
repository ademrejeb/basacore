import Link from "next/link";

import { ResetPasswordForm } from "./reset-form";

export const metadata = { title: "Choose a new password" };

interface ResetPageProps {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}

export default async function ResetPasswordPage({ searchParams }: ResetPageProps) {
  const params = await searchParams;
  const raw = params.token;
  const token = Array.isArray(raw) ? raw[0] : raw;

  return (
    <div className="login-shell">
      <section className="login-hero">
        <div className="row" style={{ gap: 12 }}>
          <span className="brand__mark">B</span>
          <span>
            <span className="brand__name">Basacore</span>
            <span className="brand__tag">Access console</span>
          </span>
        </div>
        <div>
          <p className="eyebrow">Account recovery</p>
          <h1>Choose a new password.</h1>
          <p>Passwords must be at least 12 characters and differ from recent ones.</p>
        </div>
        <p className="small muted">
          <Link href="/login">Back to sign in</Link>
        </p>
      </section>
      <section className="login-panel">
        <div className="login-card">
          <ResetPasswordForm token={token ?? ""} />
        </div>
      </section>
    </div>
  );
}
