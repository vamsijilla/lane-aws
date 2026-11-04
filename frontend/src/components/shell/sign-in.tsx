"use client";

import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { Check, ChevronRight } from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import { useState, useSyncExternalStore } from "react";
import { toast } from "sonner";

import { LaneMark } from "@/components/shell/lane-mark";
import { api, errorMessage } from "@/lib/api";
import { cognitoEnabled, startSignIn } from "@/lib/cognito";
import { type Person, useSession } from "@/lib/session";

// What each role may do, in one line, in the order people are listed
const ROLE_LINES: [string, string][] = [
  ["viewer", "reads everything; personal data masked"],
  ["underwriter", "resolves conflicts and decides; quoting at risk needs a senior"],
  [
    "senior_underwriter",
    "everything an underwriter can, plus quoting at risk and clearing a decline",
  ],
  ["ops", "runs and monitors processing; no submission content"],
  ["admin", "evals and the rules library; personal data masked"],
];

/** Until someone signs in, the sign-in page covers the app. The app still renders underneath
 * (read-only, as the API's default for no sign-in), so every route keeps rendering on the
 * server; on sign-in everything is fetched again as that person. */
export function SignInGate({ children }: { children: React.ReactNode }) {
  const session = useSession();
  // The page Cognito returns to finishes signing in on its own, uncovered. Read from the
  // location, not usePathname(): this wraps every page, which then could not be prerendered.
  const returning = useSyncExternalStore(
    noSubscribe,
    () => window.location.pathname === "/auth/callback",
    () => false,
  );
  const covered = !returning && (!session.ready || !session.signedIn);
  return (
    <>
      {/* Clipped to the screen while covered, so nothing of the app scrolls out beside it */}
      <div
        inert={covered}
        aria-hidden={covered || undefined}
        className={covered ? "h-dvh overflow-hidden" : undefined}
      >
        {children}
      </div>
      {covered ? (
        <div className="fixed inset-0 z-[100] overflow-y-auto bg-paper">
          {session.ready ? <SignIn /> : null}
        </div>
      ) : null}
    </>
  );
}

function SignIn() {
  const session = useSession();
  const reduce = useReducedMotion();
  // Real sign-in (Cognito) when the build names a user pool; else the demo's people
  const real = cognitoEnabled();
  const [leaving, setLeaving] = useState(false);
  const people = useQuery({
    queryKey: ["people"],
    enabled: !real,
    queryFn: async (): Promise<Person[]> => {
      const { data, error } = await api.GET("/api/v1/auth/people");
      if (!data) throw new Error(errorMessage(error, "The demo sign-in could not be loaded"));
      return data;
    },
  });
  const [chosen, setChosen] = useState<string | null>(null);
  const byRole = new Map((people.data ?? []).map((p) => [p.role, p]));
  const listed = ROLE_LINES.flatMap(([role, line]) => {
    const p = byRole.get(role);
    return p ? [{ person: p, line }] : [];
  });

  async function enter(p: Person) {
    setChosen(p.id);
    try {
      // The check shows for at least 150 ms; signing in itself is never held back longer
      await Promise.all([session.signIn(p), new Promise((r) => setTimeout(r, 150))]);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not sign in");
      setChosen(null);
    }
  }

  // Entrance: under 500 ms in all, nothing waits for it; none at all with reduced motion
  const rise = (delay: number) =>
    reduce
      ? {}
      : {
          initial: { opacity: 0, y: 8 },
          animate: { opacity: 1, y: 0 },
          transition: { duration: 0.22, delay, ease: [0.2, 0.8, 0.2, 1] as const },
        };

  return (
    <section aria-label="Sign in" className="flex min-h-dvh flex-col bg-paper md:flex-row">
      {/* The ink panel: what Lane is, and a lane running to the horizon */}
      <div
        style={{ background: "var(--signin-panel)", color: "var(--signin-text)" }}
        className="relative flex shrink-0 flex-col overflow-hidden px-6 pt-6 pb-0 md:w-[45%] md:justify-between md:px-14 md:pt-16"
      >
        <motion.div {...rise(0)} className="relative z-10 max-w-[30rem]">
          <div className="flex items-center gap-3">
            <span style={{ "--paper": "var(--signin-panel)" } as React.CSSProperties}>
              <LaneMark className="size-9 md:size-12" />
            </span>
            <span className="font-serif text-3xl font-medium md:text-5xl">Lane</span>
          </div>
          <p
            className="mt-3 max-w-[26rem] text-sm leading-relaxed md:mt-6 md:text-lg"
            style={{ color: "var(--signin-muted)" }}
          >
            From one agent email to an underwriting decision, with every fact traced to its source.
          </p>
        </motion.div>
        <Road />
      </div>

      {/* The app's own light surface, so signing in cuts straight into the app */}
      <div className="flex flex-1 items-start justify-center px-4 py-8 md:items-center md:px-12 md:py-16">
        <div className="w-full max-w-[440px]">
          <h1 className="font-serif text-2xl font-medium md:text-[1.75rem]">Sign in</h1>
          {real ? (
            <>
              <button
                type="button"
                disabled={leaving}
                onClick={() => {
                  setLeaving(true);
                  void startSignIn().catch(() => setLeaving(false));
                }}
                className="mt-5 flex w-full items-center justify-center gap-2 rounded-[var(--radius-control)] bg-interstate px-3 py-2.5 text-sm font-semibold text-white hover:brightness-110 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-interstate disabled:opacity-70"
              >
                {leaving ? "Opening sign-in" : "Sign in"}
                <ChevronRight size={16} aria-hidden="true" />
              </button>
              <p className="mt-2 text-xs text-graphite">
                Accounts are by invitation. Your role (underwriter, senior underwriter, viewer,
                operations or administrator) comes with your account.
              </p>
            </>
          ) : (
            <>
              <button
                type="button"
                disabled
                aria-describedby="signin-sso-note"
                className="mt-5 flex w-full cursor-not-allowed items-center justify-center rounded-[var(--radius-control)] border border-hairline bg-surface px-3 py-2 text-sm font-semibold text-graphite"
              >
                Sign in with Microsoft
              </button>
              <p id="signin-sso-note" className="mt-1.5 text-xs text-graphite">
                Not connected in this demo. In production: single sign-on, with roles from your
                directory groups.
              </p>

              <h2 className="mt-8 text-sm text-graphite">Demo sign-in</h2>
              <ul className="mt-2 divide-y divide-hairline overflow-hidden rounded-[var(--radius-panel)] border border-hairline bg-surface">
                {listed.map(({ person, line }, i) => (
                  <motion.li key={person.id} {...rise(0.1 + i * 0.04)}>
                    <button
                      type="button"
                      onClick={() => void enter(person)}
                      disabled={chosen !== null}
                      aria-label={`${person.name}, ${person.role_label}`}
                      aria-describedby={`signin-${person.id}`}
                      className={clsx(
                        "flex w-full items-center gap-3 px-4 py-3 text-left transition-colors duration-100",
                        "hover:bg-surface-sunk active:bg-hairline/60",
                        "focus-visible:relative focus-visible:z-10 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-interstate",
                        chosen === person.id && "bg-surface-sunk",
                        chosen !== null && chosen !== person.id && "opacity-50",
                      )}
                    >
                      <span className="min-w-0 flex-1">
                        <span className="block text-sm font-semibold text-ink">{person.name}</span>
                        <span id={`signin-${person.id}`} className="block text-xs text-graphite">
                          {person.role_label}: {line}
                        </span>
                      </span>
                      {chosen === person.id ? (
                        <Check size={16} className="shrink-0 text-interstate" aria-hidden="true" />
                      ) : (
                        <ChevronRight
                          size={16}
                          className="shrink-0 text-graphite"
                          aria-hidden="true"
                        />
                      )}
                    </button>
                  </motion.li>
                ))}
              </ul>
              {people.isError ? (
                <p className="mt-3 text-sm text-stop">The demo sign-in could not be loaded.</p>
              ) : null}
            </>
          )}
        </div>
      </div>
    </section>
  );
}

/** Two edge lines and a dashed centre line running to the horizon. Decoration only; the dashes
 * drift toward the viewer (lane-drift, 8 s), and stand still with reduced motion. */
function Road() {
  return (
    <svg
      viewBox="0 0 400 220"
      preserveAspectRatio="xMidYMax slice"
      aria-hidden="true"
      className="mt-6 h-24 w-full md:mt-10 md:h-[42vh]"
    >
      <path d="M40 220 L188 14" stroke="var(--signin-road)" strokeWidth="2.5" fill="none" />
      <path d="M360 220 L212 14" stroke="var(--signin-road)" strokeWidth="2.5" fill="none" />
      <path
        d="M200 220 L200 14"
        stroke="var(--signin-text)"
        strokeOpacity="0.28"
        strokeWidth="2.5"
        strokeDasharray="30 18"
        fill="none"
        className="lane-drift"
      />
    </svg>
  );
}

const noSubscribe = () => () => {};
