"use client";

import clsx from "clsx";
import { ArrowLeft, TriangleAlert } from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  createContext,
  Suspense,
  useContext,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { createPortal } from "react-dom";

import { EvidenceProvider, useLens } from "@/components/evidence/lens";
import { TOPBAR_CONTEXT } from "@/components/shell/app-shell";
import { ManageSubmission } from "@/components/submission/manage";
import { LastRerun, RerunProvider } from "@/components/submission/rerun";
import { RouteSign } from "@/components/ui/route-sign";
import { Identifier, Skeleton } from "@/components/ui/primitives";
import { formatDate, formatDateTime } from "@/lib/format";
import { useSession } from "@/lib/session";
import {
  useAnalysis,
  useConflicts,
  useMissing,
  useOverview,
  usePrefetchSubmission,
} from "@/lib/queries";

const SubmissionContext = createContext<string | null>(null);

export function useSubmissionId(): string {
  const id = useContext(SubmissionContext);
  if (!id) throw new Error("useSubmissionId outside a submission");
  return id;
}

type Tab = { href: string; label: string };
type View = { key: string; label: string; tabs: Tab[]; count?: "conflicts" | "findings" };

/** Six views in the underwriter's reading order, holding twelve tabs between them. */
export const VIEWS: View[] = [
  {
    key: "overview",
    label: "Overview",
    tabs: [
      { href: "", label: "Brief" },
      { href: "/operations", label: "Applicant and operations" },
      { href: "/coverage", label: "Coverage and losses" },
    ],
  },
  {
    key: "documents",
    label: "Documents",
    tabs: [
      { href: "/documents", label: "Documents" },
      { href: "/audit", label: "Audit" },
    ],
  },
  {
    key: "people",
    label: "Drivers and vehicles",
    tabs: [
      { href: "/drivers", label: "Drivers" },
      { href: "/vehicles", label: "Vehicles" },
    ],
  },
  {
    key: "review",
    label: "Conflicts and missing",
    count: "conflicts",
    tabs: [
      { href: "/conflicts", label: "Conflicts" },
      { href: "/missing", label: "Missing" },
    ],
  },
  { key: "rules", label: "Rules", tabs: [{ href: "/rules", label: "Rules" }] },
  {
    key: "findings",
    label: "Findings and actions",
    count: "findings",
    tabs: [
      { href: "/findings", label: "AI findings" },
      { href: "/actions", label: "Next actions" },
    ],
  },
];

function activeView(base: string, pathname: string): { view: View; tab: Tab } {
  const rest = pathname.slice(base.length);
  for (const view of VIEWS) {
    for (const tab of view.tabs) {
      if (tab.href ? rest === tab.href || rest.startsWith(`${tab.href}/`) : rest === "") {
        return { view, tab };
      }
    }
  }
  return { view: VIEWS[0], tab: VIEWS[0].tabs[0] };
}

export function SubmissionShell({ id, children }: { id: string; children: React.ReactNode }) {
  const session = useSession();
  // Operations sees processing, not submission content (the API refuses it): say so, fetch nothing
  if (session.ready && session.signedIn && !session.can("view_content"))
    return (
      <div className="mx-auto max-w-[70ch] px-8 py-12">
        <h1 className="font-serif text-2xl font-medium">Submission content is not shown</h1>
        <p className="mt-2 text-graphite">
          The {session.roleLabel.toLowerCase()} role sees how submissions are processed, not what
          they contain. Open Operations for runs and queue health.
        </p>
        <Link href="/ops" className="mt-4 inline-block text-interstate hover:underline">
          Open Operations
        </Link>
      </div>
    );
  return (
    <SubmissionContext.Provider value={id}>
      <RerunProvider>
        <EvidenceProvider submissionId={id}>
          <Suspense
            fallback={
              <div className="px-8 py-6">
                <Skeleton lines={8} />
              </div>
            }
          >
            <Frame id={id}>{children}</Frame>
          </Suspense>
        </EvidenceProvider>
      </RerunProvider>
    </SubmissionContext.Provider>
  );
}

function Frame({ id, children }: { id: string; children: React.ReactNode }) {
  const pathname = usePathname();
  const base = `/submissions/${id}`;
  const { view, tab } = activeView(base, pathname);
  const overview = useOverview(id);
  const conflicts = useConflicts(id);
  const missing = useMissing(id);
  const analysis = useAnalysis(id);
  const lens = useLens();
  const sentinel = useRef<HTMLDivElement>(null);

  const counts: Record<string, number | undefined> = {
    // Open conflicts only: one a person (or a document) settled no longer needs work
    conflicts: overview.data
      ? overview.data.submission.conflicts_open + overview.data.submission.missing
      : undefined,
    findings: analysis.data?.findings.length,
  };
  const o = overview.data;
  // Once the first screen has its data, every other tab loads behind it
  usePrefetchSubmission(
    id,
    overview.isSuccess && conflicts.isSuccess && missing.isSuccess && analysis.isSuccess,
  );
  const applicant = o?.applicant.legal_name ?? o?.submission.subject ?? "";

  const compact = useScrolledPast(sentinel);
  const reduce = useReducedMotion();
  const route =
    o?.route && o.submission.status !== "processing" ? (
      <RouteSign
        size="xs"
        outcome={o.route.outcome}
        headline={o.route.headline}
        lines={[
          // Still a potential decline until an underwriter confirms what cleared it
          o.submission.potential_decline ? "Potential decline" : null,
          o.submission.pending_confirmations ? "Change pending underwriter confirmation" : null,
        ]}
      />
    ) : null;

  return (
    <div
      className={clsx(
        "transition-[padding] duration-200 ease-out",
        lens.target && "min-[1600px]:pr-[max(min(46vw,860px),520px)]",
      )}
    >
      {/* Who and what: read once, then it scrolls away and the bar below carries it */}
      <header className="px-8 pt-5 pb-4">
        <div className="mb-2 flex items-center justify-between gap-4">
          <Link
            href="/"
            className="inline-flex items-center gap-1 text-sm text-graphite hover:text-interstate"
          >
            <ArrowLeft size={14} aria-hidden="true" /> Inbox
          </Link>
          <ManageSubmission id={id} name={applicant} />
        </div>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <h1 className="truncate font-serif text-2xl leading-tight font-medium">
            {applicant || " "}
          </h1>
          {o?.route && o.submission.status !== "processing" ? (
            <Link href={base} title="Open the brief">
              <RouteSign
                size="sm"
                outcome={o.route.outcome}
                headline={o.route.headline}
                lines={[
                  // Still a potential decline until an underwriter confirms what cleared it
                  o.submission.potential_decline ? "Potential decline" : null,
                  o.submission.pending_confirmations
                    ? "Change pending underwriter confirmation"
                    : null,
                  o.route.outcome === "ready" && o.route.checks_at_quote.length
                    ? `${o.route.checks_at_quote.length} ${o.route.checks_at_quote.length === 1 ? "check" : "checks"} at quote`
                    : null,
                ]}
              />
            </Link>
          ) : null}
        </div>
        {o ? (
          <p className="mt-2 flex flex-wrap items-center gap-x-5 gap-y-1 text-sm text-graphite">
            {[
              o.applicant.dot_number ? (
                <span key="dot">
                  USDOT <Identifier value={o.applicant.dot_number} className="text-ink" />
                </span>
              ) : null,
              o.applicant.mc_number ? (
                <span key="mc">
                  MC <Identifier value={o.applicant.mc_number} className="text-ink" />
                </span>
              ) : null,
              o.applicant.mailing_city ? (
                <span key="city">
                  {o.applicant.mailing_city}, {o.applicant.mailing_state}
                </span>
              ) : null,
              o.applicant.effective_date ? (
                <span key="eff">Effective {formatDate(o.applicant.effective_date)}</span>
              ) : null,
              <span key="from">
                From {o.applicant.agency ?? o.submission.sender_name ?? o.submission.sender_email}
                {o.applicant.conflicted.includes("agency.name") ? (
                  <Link
                    href={`${base}/conflicts`}
                    title="The documents name the agency differently"
                    aria-label="Agency name conflict"
                    className="ml-1 inline-flex translate-y-[2px] text-warning hover:text-ink"
                  >
                    <TriangleAlert size={14} aria-hidden="true" />
                  </Link>
                ) : null}
                , received {formatDateTime(o.submission.received_at)}
              </span>,
            ].filter(Boolean)}
          </p>
        ) : null}
      </header>
      <div ref={sentinel} aria-hidden="true" className="h-px" />
      <TopBarContext>
        <AnimatePresence initial={false}>
          {compact ? (
            <motion.div
              key="who"
              initial={reduce ? false : { opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={reduce ? undefined : { opacity: 0, y: 6 }}
              transition={{ duration: 0.16, ease: "easeOut" }}
              className="flex min-w-0 items-center gap-3"
            >
              <span
                title={applicant}
                className="min-w-[6rem] shrink truncate font-serif text-lg font-medium"
              >
                {applicant}
              </span>
              {route ? (
                <Link
                  href={base}
                  title="Open the brief"
                  // Only where it fits beside the name; otherwise the name alone
                  className="hidden shrink-0 whitespace-nowrap @[36rem]:block"
                >
                  {route}
                </Link>
              ) : null}
            </motion.div>
          ) : null}
        </AnimatePresence>
      </TopBarContext>
      <div
        data-compact={compact || undefined}
        className={clsx("sticky top-14 z-20 border-b border-hairline bg-paper px-2")}
      >
        <div
          className={clsx(
            "flex h-11 items-center gap-3 px-6",
            view.tabs.length > 1 && "border-b border-hairline",
          )}
        >
          <nav
            aria-label="Submission views"
            className="flex h-full min-w-0 flex-1 items-stretch gap-1 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
          >
            {VIEWS.map((v) => {
              const active = v.key === view.key;
              const count = v.count ? counts[v.count] : undefined;
              return (
                <Link
                  key={v.key}
                  href={`${base}${v.tabs[0].href}`}
                  aria-current={active ? "page" : undefined}
                  className={clsx(
                    "relative inline-flex shrink-0 items-center gap-1 px-3 text-base font-medium whitespace-nowrap",
                    active ? "text-ink" : "text-graphite hover:text-ink",
                  )}
                >
                  {v.label}
                  {count !== undefined && count > 0 ? (
                    <span className="text-sm text-graphite tabular-nums">{count}</span>
                  ) : null}
                  {active ? (
                    <span className="absolute inset-x-3 bottom-0 h-[2px] bg-interstate" />
                  ) : null}
                </Link>
              );
            })}
          </nav>
        </div>
        {view.tabs.length > 1 ? (
          // Second level: smaller, under the main row's rule, with its own short underline
          <nav aria-label={`${view.label} tabs`} className="flex h-9 items-stretch gap-6 px-9">
            {view.tabs.map((t) => {
              const active = t.href === tab.href;
              return (
                <Link
                  key={t.href}
                  href={`${base}${t.href}`}
                  aria-current={active ? "page" : undefined}
                  className={clsx(
                    "-mb-px inline-flex items-center border-b-[1.5px] text-sm whitespace-nowrap",
                    active
                      ? "border-interstate text-ink"
                      : "border-transparent text-graphite hover:text-ink",
                  )}
                >
                  {t.label}
                </Link>
              );
            })}
          </nav>
        ) : null}
      </div>
      <div className="px-8 pt-6 pb-16">
        <LastRerun />
        {children}
      </div>
    </div>
  );
}

/** True once the element has scrolled up under the app's top bar (56 px): the full header
 * is out of view, so the sticky bar shows who and what instead. */
function useScrolledPast(ref: React.RefObject<HTMLElement | null>): boolean {
  const [past, setPast] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new IntersectionObserver(
      ([entry]) => setPast(!entry.isIntersecting && entry.boundingClientRect.top < 56),
      { rootMargin: "-56px 0px 0px 0px" },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref]);
  return past;
}

/** Render into the app's top bar (left side), once it exists in the page. */
function TopBarContext({ children }: { children: React.ReactNode }) {
  const slot = useSyncExternalStore(
    noSubscribe,
    () => document.getElementById(TOPBAR_CONTEXT),
    () => null, // nothing to portal into on the server
  );
  return slot ? createPortal(children, slot) : null;
}

const noSubscribe = () => () => {};
