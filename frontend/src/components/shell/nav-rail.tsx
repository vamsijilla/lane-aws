"use client";

import clsx from "clsx";
import { Activity, BookOpenCheck, Gauge, Inbox, type LucideIcon, Milestone } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

type NavItem = { href: string; label: string; icon: LucideIcon };

const NAV: NavItem[] = [
  { href: "/", label: "Inbox", icon: Inbox },
  { href: "/evals", label: "Evals", icon: Gauge },
  { href: "/rules-library", label: "Rules library", icon: BookOpenCheck },
  { href: "/ops", label: "Operations", icon: Activity },
  { href: "/how-lane-works", label: "How Lane works", icon: Milestone },
];

export function NavRail() {
  const pathname = usePathname();
  return (
    <nav aria-label="Main" className="flex flex-col gap-0.5 p-2">
      {NAV.map(({ href, label, icon: Icon }) => {
        const active =
          href === "/"
            ? pathname === "/" || pathname.startsWith("/submissions")
            : pathname.startsWith(href);
        return (
          <Link
            key={href}
            href={href}
            aria-current={active ? "page" : undefined}
            className={clsx(
              // Active: ink text and a thin accent rule at the left, no tinted block
              "flex items-center gap-2.5 px-2.5 py-2 text-base",
              active
                ? "font-medium text-ink shadow-[inset_2px_0_0_var(--interstate)]"
                : "text-graphite hover:text-ink",
            )}
          >
            <Icon size={16} strokeWidth={2} aria-hidden="true" />
            {label}
          </Link>
        );
      })}
    </nav>
  );
}
