import Link from "next/link";

import { AcceptInviteForm } from "./accept-form";

export const metadata = { title: "Accept your invitation" };

interface InvitePageProps {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}

export default async function AcceptInvitePage({ searchParams }: InvitePageProps) {
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
          <p className="eyebrow">You are invited</p>
          <h1>Finish setting up your account.</h1>
          <p>Choose a password to activate the account and sign in to your workspace.</p>
        </div>
        <p className="small muted">
          <Link href="/login">Already set up? Sign in</Link>
        </p>
      </section>
      <section className="login-panel">
        <div className="login-card">
          <AcceptInviteForm token={token ?? ""} />
        </div>
      </section>
    </div>
  );
}
