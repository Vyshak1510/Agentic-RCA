"use client";

import clsx from "clsx";
import Link from "next/link";
import { Activity, History, Settings2 } from "lucide-react";
import { usePathname } from "next/navigation";
import { PropsWithChildren } from "react";

import { UserContext } from "@/lib/types";

type Props = PropsWithChildren<{
  user: UserContext;
}>;

const links = [
  { href: "/incidents/past", label: "Past Incidents", icon: History },
  { href: "/incidents/ongoing", label: "Ongoing", icon: Activity },
  { href: "/settings", label: "Settings", icon: Settings2 },
];

export function NavShell({ user, children }: Props) {
  const pathname = usePathname();

  return (
    <div className="flex h-screen min-h-screen flex-col overflow-hidden px-4 py-4 md:px-6">
      <header className="mb-4 rounded-[28px] border border-[#0d5356]/35 bg-[linear-gradient(135deg,#0f6a6d_0%,#0e5666_52%,#16344d_100%)] px-5 py-4 text-white shadow-[0_18px_40px_rgba(15,55,77,0.22)]">
        <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
          <div>
            <h1 className="text-xl font-bold tracking-tight">RCA Incident Console</h1>
            <p className="text-sm text-[#d8f2ec]">Tenant: {user.tenant} · Role: {user.role}</p>
          </div>
          <nav className="flex flex-wrap gap-2">
            {links.map((link) => {
              const active =
                link.href === "/incidents/past"
                  ? pathname.startsWith("/incidents/") && !pathname.startsWith("/incidents/ongoing")
                  : pathname === link.href;

              return (
                <Link
                  key={link.href}
                  href={link.href}
                  className={clsx(
                    "inline-flex items-center gap-2 rounded-full border px-3 py-2 text-sm transition",
                    active
                      ? "border-white/65 bg-white text-[#12425f] shadow-[0_10px_24px_rgba(255,255,255,0.18)]"
                      : "border-white/18 bg-white/10 text-white hover:border-white/30 hover:bg-white/16"
                  )}
                >
                  <span
                    className={clsx(
                      "inline-flex h-7 w-7 items-center justify-center rounded-full",
                      active ? "bg-[#e8f7f3] text-[#0f6a6d]" : "bg-white/16 text-white"
                    )}
                  >
                    <link.icon className="h-3.5 w-3.5" />
                  </span>
                  {link.label}
                </Link>
              );
            })}
          </nav>
        </div>
      </header>
      <main className="flex min-h-0 flex-1 flex-col overflow-auto">{children}</main>
    </div>
  );
}
