import "./globals.css";

import type { Metadata } from "next";
import type { ReactNode } from "react";

import { ErrorState } from "@/components/error-state";
import { NavShell } from "@/components/nav-shell";
import { fetchMe } from "@/lib/api";

export const metadata: Metadata = {
  title: "RCA Incident Console",
  description: "Agentic RCA operations console"
};

export default async function RootLayout({
  children
}: Readonly<{
  children: ReactNode;
}>) {
  try {
    const user = await fetchMe();
    return (
      <html lang="en">
        <body>
          <NavShell user={user}>{children}</NavShell>
        </body>
      </html>
    );
  } catch (error) {
    return (
      <html lang="en">
        <body>
          <div className="p-10">
            <ErrorState message={error instanceof Error ? error.message : "Unable to load user context"} />
          </div>
        </body>
      </html>
    );
  }
}
