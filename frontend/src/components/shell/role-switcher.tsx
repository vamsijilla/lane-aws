"use client";

import { UserRound } from "lucide-react";

import { useSession } from "@/lib/session";

/** Who is signed in, and a way to sign in as someone else. Production signs in with SSO. */
export function RoleSwitcher() {
  const session = useSession();
  return (
    <span className="inline-flex items-center gap-1.5 text-sm">
      <UserRound size={15} aria-hidden="true" className="text-graphite" />
      <span className="font-semibold text-ink">
        {session.name ? `${session.name}, ${session.roleLabel}` : session.roleLabel}
      </span>
      <button
        type="button"
        onClick={() => void session.signOut()}
        className="ml-1 text-interstate hover:underline"
      >
        Switch
      </button>
    </span>
  );
}
