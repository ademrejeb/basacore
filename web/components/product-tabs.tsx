import Link from "next/link";
import { Boxes, PlugZap, Users } from "lucide-react";

const TABS = [
  { key: "overview", label: "Overview", icon: Boxes, segment: "" },
  { key: "integration", label: "Setup", icon: PlugZap, segment: "/integration" },
  { key: "workspaces", label: "Workspaces", icon: Users, segment: "/clients" },
] as const;

export function ProductTabs({
  id,
  active,
}: {
  id: string;
  active: (typeof TABS)[number]["key"];
}) {
  return (
    <nav className="panel-tabs" aria-label="Application sections">
      {TABS.map((tab) => {
        const Icon = tab.icon;
        const isActive = tab.key === active;
        return (
          <Link
            key={tab.key}
            className={`panel-tabs__item${isActive ? " is-active" : ""}`}
            href={`/applications/${id}${tab.segment}`}
            aria-current={isActive ? "page" : undefined}
          >
            <Icon size={15} />
            {tab.label}
          </Link>
        );
      })}
    </nav>
  );
}
