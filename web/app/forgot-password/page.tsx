import Link from "next/link";

import { ForgotPasswordForm } from "./forgot-form";

export const metadata = { title: "Reset your password" };

export default function ForgotPasswordPage() {
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
          <h1>Reset your password.</h1>
          <p>
            Enter the email address on your account. If it exists, we will send a
            single-use link that expires in 60 minutes.
          </p>
        </div>
        <p className="small muted">
          <Link href="/login">Back to sign in</Link>
        </p>
      </section>
      <section className="login-panel">
        <div className="login-card">
          <ForgotPasswordForm />
        </div>
      </section>
    </div>
  );
}
