import Link from "next/link";
import { Suspense } from "react";

import { ApiStatus } from "@/components/shell/api-status";
import { LaneMark } from "@/components/shell/lane-mark";
import { NavRail } from "@/components/shell/nav-rail";
import { RoleSwitcher } from "@/components/shell/role-switcher";
import { ThemeToggle } from "@/components/shell/theme-toggle";
import { Commands } from "@/components/ui/commands";

export const TOPBAR_CONTEXT = "topbar-context";

export function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="grid min-h-dvh grid-cols-[13rem_1fr]">
      {/* The column carries the background the full page height; the rail inside stays put */}
      <div className="border-r border-hairline bg-paper">
        <aside className="sticky top-0 flex h-dvh flex-col">
          <div className="flex h-14 items-center border-b border-hairline px-3">
            <Link
              href="/"
              aria-label="Lane, go to the Inbox"
              className="flex items-center gap-1.5 rounded-[var(--radius-cell)] px-1.5 py-0.5 text-lg font-semibold tracking-tight hover:text-interstate focus-visible:ring-2 focus-visible:ring-interstate focus-visible:outline-none"
            >
              <LaneMark className="size-6" />
              Lane
            </Link>
          </div>
          <Suspense fallback={<nav aria-label="Main" className="h-40" />}>
            <NavRail />
          </Suspense>
          <div className="mt-auto border-t border-hairline px-4 py-3 text-xs text-graphite">
            <p className="font-semibold text-ink">Knight Specialty</p>
            <p>Trucking program</p>
          </div>
        </aside>
      </div>
      <div className="flex min-w-0 flex-col">
        <header className="sticky top-0 z-30 flex h-14 shrink-0 items-center gap-3 border-b border-hairline bg-paper px-6">
          {/* A page can show what it is about here once its own header scrolls away */}
          {/* It gives way to Go to: what does not fit is clipped, never drawn under it */}
          <div
            id={TOPBAR_CONTEXT}
            className="@container flex min-w-0 flex-1 items-center overflow-hidden"
          />
          <Suspense fallback={<span className="h-8 w-28" aria-hidden="true" />}>
            <Commands />
          </Suspense>
          <span className="h-5 w-px bg-hairline" aria-hidden="true" />
          <ApiStatus />
          <span className="h-5 w-px bg-hairline" aria-hidden="true" />
          <RoleSwitcher />
          <ThemeToggle />
        </header>
        <main className="min-w-0 flex-1">{children}</main>
      </div>
    </div>
  );
}
