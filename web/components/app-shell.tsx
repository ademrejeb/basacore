"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import {
  Activity,
  Building2,
  CreditCard,
  FileClock,
  LayoutDashboard,
  LogOut,
  Settings,
  ShieldCheck,
  Sparkles,
  Users,
} from "lucide-react";
import {
  Avatar,
  Button,
  Sidebar,
  ThemeToggle,
} from "@adem.rejeb/ui";

import type { Session } from "@/lib/types";

const NAV_GROUPS: Array<{
  label: string;
  items: Array<{ href: string; label: string; icon: React.ComponentType<{ size?: number }>; permission?: string }>;
}> = [
  {
    label: "Overview",
    items: [
      { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard, permission: "dashboard.read" },
    ],
  },
  {
    label: "Manage",
    items: [
      { href: "/applications", label: "Applications", icon: Sparkles, permission: "applications.read" },
      { href: "/organizations", label: "Organizations", icon: Building2, permission: "organizations.read" },
      { href: "/tenants", label: "Workspaces", icon: Users, permission: "tenants.read" },
    ],
  },
  {
    label: "Administration",
    items: [
      { href: "/billing", label: "Billing", icon: CreditCard, permission: "billing.read" },
      { href: "/access", label: "Access", icon: ShieldCheck, permission: "roles.read" },
      { href: "/audit", label: "Activity log", icon: FileClock, permission: "audit.read" },
    ],
  },
  {
    label: "System",
    items: [
      { href: "/operations", label: "System health", icon: Activity, permission: "infrastructure.read" },
      { href: "/settings", label: "Settings", icon: Settings },
    ],
  },
];

export function AppShell({
  session,
  children,
}: {
  session: Session;
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  const router = useRouter();
  const [loggingOut, setLoggingOut] = useState(false);

  const allowed = (permission?: string) =>
    !permission || session.platform_permissions.includes(permission);

  const groups = NAV_GROUPS.map((group) => ({
    ...group,
    items: group.items.filter((item) => allowed(item.permission)),
  })).filter((group) => group.items.length > 0);

  const current = groups
    .flatMap((group) => group.items)
    .filter((item) => pathname === item.href || pathname.startsWith(`${item.href}/`))
    .sort((a, b) => b.href.length - a.href.length)[0];

  // Navigation hides pages the user cannot open, but a deep link would still
  // render and then fail with a raw API error. Redirect before that happens.
  const requiredPermission = NAV_GROUPS.flatMap((group) => group.items)
    .filter(
      (item) =>
        item.permission && (pathname === item.href || pathname.startsWith(`${item.href}/`)),
    )
    .sort((a, b) => b.href.length - a.href.length)[0]?.permission;

  useEffect(() => {
    if (!requiredPermission) return;
    if (session.platform_permissions.includes(requiredPermission)) return;
    if (pathname !== "/dashboard") router.replace("/dashboard");
  }, [requiredPermission, pathname, router, session.platform_permissions]);

  async function logout() {
    setLoggingOut(true);
    try {
      await fetch("/api/proxy/v1/auth/logout", {
        method: "POST",
        headers: { "content-type": "application/json" },
        credentials: "include",
      });
    } finally {
      router.replace("/login");
      router.refresh();
    }
  }

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Skip to content
      </a>
      <aside className="app-sidebar">
        <Sidebar defaultOpen={false}>
          <Sidebar.Header>
            <Link className="brand" href="/dashboard">
              <span className="brand__mark">B</span>
              <span>
                <span className="brand__name">Basacore</span>
                <span className="brand__tag">Access console</span>
              </span>
            </Link>
          </Sidebar.Header>
          <Sidebar.Nav aria-label="Primary">
            {groups.map((group) => (
              <Sidebar.Section key={group.label} label={group.label}>
                {group.items.map((item) => {
                  const Icon = item.icon;
                  const active = current?.href === item.href;
                  return (
                    <Sidebar.Link
                      key={item.href}
                      href={item.href}
                      active={active}
                      icon={<Icon size={17} />}
                      onClick={(event) => {
                        if (
                          event.metaKey ||
                          event.ctrlKey ||
                          event.shiftKey ||
                          event.altKey ||
                          event.button !== 0
                        ) {
                          return;
                        }
                        event.preventDefault();
                        router.push(item.href);
                      }}
                    >
                      {item.label}
                    </Sidebar.Link>
                  );
                })}
              </Sidebar.Section>
            ))}
          </Sidebar.Nav>
          <Sidebar.Footer>
            <div className="sidebar-footer">
              <p className="small muted">Basacore 0.1.0</p>
            </div>
          </Sidebar.Footer>
        </Sidebar>
      </aside>

      <div className="app-main">
        <header className="app-topbar">
          <div className="app-topbar__crumbs">
            <span>Basacore</span>
            <span aria-hidden>/</span>
            <strong>{current?.label ?? "Workspace"}</strong>
          </div>
          <div className="app-topbar__actions">
            <ThemeToggle />
            <div className="app-user">
              <Avatar
                name={session.user.full_name}
                src={session.user.avatar_url ?? undefined}
                size="sm"
              />
              <span>
                <span className="app-user__name">{session.user.full_name}</span>
                <span className="app-user__meta">{session.user.email}</span>
              </span>
            </div>
            <Button
              variant="ghost"
              size="sm"
              loading={loggingOut}
              onClick={logout}
              aria-label="Sign out"
            >
              <LogOut size={16} />
              <span className="app-signout__label">Sign out</span>
            </Button>
          </div>
        </header>
        <main className="app-content" id="main-content" tabIndex={-1}>
          {children}
        </main>
      </div>
    </div>
  );
}
