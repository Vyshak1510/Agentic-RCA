import Link from "next/link";
import { PropsWithChildren } from "react";

import { UserContext } from "@/lib/types";

type Props = PropsWithChildren<{
  user: UserContext;
}>;

const links = [
  { href: "/incidents/past", label: "Past Incidents" },
  { href: "/incidents/ongoing", label: "Ongoing" },
  { href: "/settings", label: "Settings" }
];

export function NavShell({ user, children }: Props) {
  return (
    <div className="min-h-screen px-6 py-5 md:px-12">
      <header className="mb-6 rounded-2xl bg-ink px-6 py-4 text-white shadow-panel">
        <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
          <div>
            <h1 className="text-xl font-bold tracking-tight">RCA Incident Console</h1>
            <p className="text-sm text-mint">Tenant: {user.tenant} · Role: {user.role}</p>
          </div>
          <nav className="flex gap-2">
            {links.map((link) => (
              <Link key={link.href} href={link.href} className="rounded-md border border-white/25 px-3 py-1 text-sm hover:bg-white/10">
                {link.label}
              </Link>
            ))}
          </nav>
        </div>
      </header>
      <main>{children}</main>
    </div>
  );
}
