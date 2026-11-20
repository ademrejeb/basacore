import { redirect } from "next/navigation";

import { getSession } from "@/lib/api";

import { LoginForm } from "./login-form";

export const metadata = { title: "Sign in" };

interface LoginPageProps {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}

function first(value: string | string[] | undefined): string | undefined {
  return Array.isArray(value) ? value[0] : value;
}

export default async function LoginPage({ searchParams }: LoginPageProps) {
  const params = await searchParams;
  const returnTo = first(params.return_to);
  const emailParam = first(params.email);
  const appName = first(params.app_name);
  const tenantName = first(params.tenant_name);
  const reauth = first(params.reauth) === "1";

  const session = await getSession();
  const continueHref = returnTo
    ? `/auth/continue?return_to=${encodeURIComponent(returnTo)}`
    : "/dashboard";

  // A live console session is reused unless the application asked for
  // re-authentication. In that case the form is always shown, and the replay of
  // the authorization request (without ``prompt``) completes the sign-in.
  if (session && !reauth) redirect(continueHref);

  const showDemo = process.env.SHOW_DEMO_CREDENTIALS === "1";
  const initialEmail =
    emailParam ?? session?.user.email ?? (returnTo ? "" : showDemo ? "admin@basa.dev" : "");

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
          {appName ? (
            <>
              <p className="eyebrow">Sign in</p>
              <h1>Continue to {appName}</h1>
              <p>
                {tenantName ? (
                  <>
                    You are signing in to the <strong>{tenantName}</strong> workspace. We
                    will check that you have access before continuing.
                  </>
                ) : (
                  <>
                    We will check that you have access, then return you to the
                    application.
                  </>
                )}
              </p>
            </>
          ) : (
            <>
              <p className="eyebrow">Basacore</p>
              <h1>One place to manage access to your applications.</h1>
              <p>
                Add applications, organize the companies that use them, and give each one a
                separate workspace. Every important change is recorded.
              </p>
            </>
          )}
          <ul className="login-points">
            <li>
              <strong>Applications.</strong> Connect the applications your customers sign
              in to.
            </li>
            <li>
              <strong>Organizations.</strong> Keep each company and its billing details in
              one place.
            </li>
            <li>
              <strong>Workspaces.</strong> Give each organization its own private space per
              application.
            </li>
            <li>
              <strong>Activity log.</strong> See who changed what, and when.
            </li>
          </ul>
        </div>

        <p className="small muted">Basacore 0.1.0</p>
      </section>

      <section className="login-panel">
        <div className="login-card">
          <LoginForm initialEmail={initialEmail} returnTo={returnTo ?? null} />
          {showDemo ? (
            <div className="login-demo">
              <strong>Local demo account</strong>
              <br />
              <code>admin@basa.dev</code> / <code>Adm1n!Basa#2026</code>
              <br />
              Workspace users share the same password in the demo data.
            </div>
          ) : null}
        </div>
      </section>
    </div>
  );
}
