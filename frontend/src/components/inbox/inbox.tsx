"use client";

import clsx from "clsx";
import { MailOpen, Search } from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { SubmissionStatus } from "@/components/inbox/submission-status";
import { Kbd } from "@/components/ui/primitives";
import { RouteSign } from "@/components/ui/route-sign";
import { listSubmissions, type SubmissionSummary, uploadEmail } from "@/lib/api";
import { DECISION_LABELS } from "@/components/submission/decision";
import { formatDateTime, formatTime, personName } from "@/lib/format";
import { useSession } from "@/lib/session";
import { usePrefetchFirstScreen } from "@/lib/queries";
import {
  age,
  arrange,
  counts,
  type SortKey,
  TURNAROUND_HOURS,
  type ViewKey,
  VIEWS,
  viewOf,
} from "@/lib/queue";

const POLL_BUSY_MS = 2_000;
const POLL_IDLE_MS = 15_000;
const COLUMNS = "grid-cols-[8rem_minmax(0,1.2fr)_minmax(15rem,1fr)_minmax(10rem,0.7fr)_8rem]";

type Load =
  | { kind: "loading" }
  | { kind: "error"; message: string }
  | { kind: "ready"; items: SubmissionSummary[] };

type Notice = { tone: "ok" | "info" | "stop"; text: string } | null;

/** The inbox: a work queue. Its view, search and sort live in the link, so a view can be shared. */
export function Inbox() {
  return (
    <Suspense fallback={null}>
      <Queue />
    </Suspense>
  );
}

function Queue() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const view = viewOf(params.get("view"));
  const query = params.get("q") ?? "";
  const sort =
    (["received", "age", "route"] as const).find((k) => k === params.get("sort")) ?? "received";
  const setParam = (key: string, value: string | null) => {
    const next = new URLSearchParams(params.toString());
    if (value) next.set(key, value);
    else next.delete(key);
    const qs = next.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  };
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [notice, setNotice] = useState<Notice>(null);
  const [uploading, setUploading] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async (signal?: AbortSignal) => {
    try {
      const items = await listSubmissions(signal);
      setLoad({ kind: "ready", items });
      return items;
    } catch (error) {
      if (signal?.aborted) return null;
      setLoad({
        kind: "error",
        message: error instanceof Error ? error.message : "Could not load submissions",
      });
      return null;
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      const items = await refresh(controller.signal);
      if (controller.signal.aborted) return;
      const busy = items?.some(
        (s) => s.status === "processing" || s.latest_run?.status === "running",
      );
      timer = setTimeout(() => void tick(), busy ? POLL_BUSY_MS : POLL_IDLE_MS);
    };
    void tick();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [refresh]);

  async function onFile(file: File) {
    setUploading(true);
    setNotice(null);
    try {
      const result = await uploadEmail(file);
      setNotice(
        result.created
          ? {
              tone: "ok",
              text: `Email received: ${result.unique_files} files kept, ${result.duplicates} duplicates merged${
                result.rejected ? `, ${result.rejected} rejected` : ""
              }. Lane is reading it now.`,
            }
          : { tone: "info", text: "This email was already received. Nothing new was added." },
      );
      await refresh();
    } catch (error) {
      setNotice({
        tone: "stop",
        text: error instanceof Error ? error.message : "Upload failed",
      });
    } finally {
      setUploading(false);
      if (input.current) input.current.value = "";
    }
  }

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="font-serif text-2xl font-medium">Inbox</h1>
          <p className="text-graphite">
            Agent submissions received by email. First decision within {TURNAROUND_HOURS} hours of
            receipt.
          </p>
        </div>
        <div>
          <input
            ref={input}
            type="file"
            accept=".eml,message/rfc822"
            className="sr-only"
            id="eml-upload"
            onChange={(e) => {
              const file = e.target.files?.[0];
              if (file) void onFile(file);
            }}
          />
          <label
            htmlFor="eml-upload"
            aria-disabled={uploading}
            className={clsx(
              "inline-flex cursor-pointer items-center rounded-[var(--radius-control)] bg-interstate px-3.5 py-2 text-base font-semibold text-on-interstate",
              "hover:brightness-110 focus-within:outline-2 focus-within:outline-offset-2 focus-within:outline-interstate dark:text-paper",
              uploading && "pointer-events-none opacity-60",
            )}
          >
            {uploading ? "Uploading" : "Upload email"}
          </label>
        </div>
      </div>

      {notice && (
        <p
          role="status"
          className={clsx(
            "rounded-[var(--radius-panel)] border px-4 py-3",
            notice.tone === "ok" && "border-guide/40 bg-guide-soft text-ink",
            notice.tone === "info" && "border-interstate/40 bg-interstate-soft text-ink",
            notice.tone === "stop" && "border-stop/40 bg-stop-soft text-ink",
          )}
        >
          {notice.text}
        </p>
      )}

      <Toolbar
        items={load.kind === "ready" ? load.items : []}
        view={view}
        query={query}
        sort={sort}
        onView={(v) => setParam("view", v === "all" ? null : v)}
        onQuery={(q) => setParam("q", q || null)}
        onSort={(k) => setParam("sort", k === "received" ? null : k)}
      />

      <section aria-label="Submissions" className="border-t border-hairline">
        <div
          aria-hidden="true"
          className={clsx(
            "grid gap-4 border-b border-hairline px-3 py-2.5 text-sm text-graphite",
            COLUMNS,
          )}
        >
          <span>Received</span>
          <span>Applicant</span>
          <span>Recommendation</span>
          <span>Signals</span>
          <span>Status</span>
        </div>
        <Body load={load} view={view} query={query} sort={sort} />
      </section>
    </div>
  );
}

function Body({
  load,
  view,
  query,
  sort,
}: {
  load: Load;
  view: ViewKey;
  query: string;
  sort: SortKey;
}) {
  const router = useRouter();
  const reduce = useReducedMotion();
  const [active, setActive] = useState(0);
  const now = useNow(30_000);
  const items = useMemo(
    () => (load.kind === "ready" ? arrange(load.items, { view, query, sort }) : []),
    [load, view, query, sort],
  );
  const current = Math.min(active, Math.max(items.length - 1, 0));

  // J and K move through the queue, Enter opens; "/" goes to search (handled by the toolbar)
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.metaKey || e.ctrlKey || e.altKey || !items.length) return;
      if (e.target instanceof HTMLElement && e.target.closest("input, textarea, select")) return;
      if (e.key === "j") setActive((i) => Math.min(i + 1, items.length - 1));
      else if (e.key === "k") setActive((i) => Math.max(i - 1, 0));
      else if (
        e.key === "Enter" &&
        items[current] &&
        !(e.target instanceof HTMLElement && e.target.closest("a, button"))
      )
        router.push(`/submissions/${items[current].id}`);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [items, current, router]);

  if (load.kind === "loading") {
    return (
      <div aria-busy="true" aria-label="Loading submissions">
        {[0, 1].map((i) => (
          <div key={i} className={clsx("grid gap-4 px-4 py-4", COLUMNS)}>
            {[0, 1, 2, 3, 4, 5].map((j) => (
              <span key={j} className="h-4 rounded-[var(--radius-cell)] bg-surface-sunk" />
            ))}
          </div>
        ))}
      </div>
    );
  }
  if (load.kind === "error") {
    return (
      <div className="flex flex-col items-center gap-2 px-6 py-14 text-center">
        <p className="font-display text-md font-semibold">Lane service is not reachable</p>
        <p className="max-w-md text-graphite">
          {load.message}. Start the API with <code className="font-mono">make dev</code> and this
          list will refresh on its own.
        </p>
      </div>
    );
  }
  if (load.items.length === 0) {
    return (
      <div className="flex flex-col items-center gap-3 px-6 py-16 text-center">
        <MailOpen size={28} strokeWidth={1.5} className="text-graphite" aria-hidden="true" />
        <p className="font-display text-md font-semibold">No submissions yet</p>
        <p className="max-w-md text-graphite">
          Upload an agent email (.eml). Lane reads every attachment, works out what each document
          really is, and lists the submission here.
        </p>
      </div>
    );
  }
  if (!items.length) {
    return (
      <div className="px-6 py-12 text-center text-graphite">
        {query ? `Nothing in this view matches "${query}".` : "Nothing in this view right now."}
      </div>
    );
  }
  return (
    <ul>
      <AnimatePresence initial={false}>
        {items.map((s, i) => (
          <motion.li
            key={s.id}
            layout={!reduce}
            initial={reduce ? false : { opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={reduce ? undefined : { opacity: 0 }}
            transition={{ duration: 0.2, ease: "easeOut" }}
            aria-current={i === current ? "true" : undefined}
            className={clsx(
              "border-b border-hairline last:border-b-0 [contain-intrinsic-size:auto_76px] [content-visibility:auto]",
              i === current && "shadow-[inset_3px_0_0_var(--interstate)]",
            )}
          >
            <Row s={s} now={now} />
          </motion.li>
        ))}
      </AnimatePresence>
    </ul>
  );
}

const dateFmt = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric" });

function Row({ s, now }: { s: SubmissionSummary; now: number }) {
  const received = new Date(s.received_at);
  const processed = s.documents > 0;
  const warm = usePrefetchFirstScreen();
  // Prefetch only what this role may read (Operations sees no submission content)
  const canRead = useSession().can("view_content");
  const waiting = age(s.received_at, now);
  return (
    <>
      <Link
        href={`/submissions/${s.id}`}
        onMouseEnter={processed && canRead ? () => warm(s.id) : undefined}
        onFocus={processed && canRead ? () => warm(s.id) : undefined}
        className={clsx("grid items-center gap-4 px-3 py-4 hover:bg-surface/70", COLUMNS)}
      >
        <span className="leading-tight">
          <span className="block">
            {dateFmt.format(received)}, {formatTime(received)}
          </span>
          {/* A decided submission is never overdue: the first decision is made */}
          {s.decision ? null : (
            <span
              className={clsx(
                "text-sm",
                waiting.state === "overdue"
                  ? "text-stop"
                  : waiting.state === "soon"
                    ? "text-warning"
                    : "text-graphite",
              )}
              title={`Received ${waiting.age} ago. First decision due within ${TURNAROUND_HOURS} hours.`}
            >
              {waiting.left}
            </span>
          )}
        </span>
        <span className="min-w-0 leading-tight">
          <span className="block truncate font-medium" title={s.subject}>
            {s.applicant ?? (s.subject || "(no subject)")}
          </span>
          <span className="mt-0.5 block truncate text-sm text-graphite">
            {[s.sender_name ?? s.sender_email, s.agency].filter(Boolean).join(", ")}
            {processed ? `, ${s.documents} documents from ${s.files} files` : ""}
          </span>
        </span>
        <span className="min-w-0">
          {s.recommendation_headline && s.recommendation ? (
            <RouteSign
              size="xs"
              outcome={s.recommendation}
              headline={s.recommendation_headline}
              lines={[
                s.pending_confirmations
                  ? "Potential decline until a cleared rule is confirmed"
                  : s.potential_decline
                    ? "Potential decline"
                    : null,
              ]}
            />
          ) : (
            <span className="text-sm text-graphite">{processed ? "" : "Reading"}</span>
          )}
        </span>

        <Signals s={s} />
        <span>
          <SubmissionStatus submission={s} />
        </span>
      </Link>
    </>
  );
}

function Signals({ s }: { s: SubmissionSummary }) {
  const items: { text: string; tone: "warn" | "muted" }[] = [];
  if (s.decision)
    items.push({
      text: `Decided: ${DECISION_LABELS[s.decision] ?? s.decision}, ${personName(s.decided_by)}, ${s.decided_at ? formatDateTime(s.decided_at) : ""}`,
      tone: "muted",
    });
  if (s.senior_review)
    items.push({
      text: `Senior review requested by ${personName(s.senior_review.by)}`,
      tone: "warn",
    });
  const other = s.related_applicant ?? "an earlier submission";
  if (s.relation === "possible_follow_up")
    items.push({ text: `Possible follow-up to ${other}`, tone: "warn" });
  if (s.relation === "same_files") items.push({ text: `Same files as ${other}`, tone: "muted" });
  if (s.updated_by_reply)
    items.push({
      text: `Updated by agent reply, answered ${s.points_answered} of ${s.points_requested} points${s.points_new ? `, ${s.points_new} new ${s.points_new === 1 ? "question" : "questions"}` : ""}`,
      tone: "muted",
    });
  if (s.pending_confirmations)
    items.push({
      text: `${s.pending_confirmations} rule ${s.pending_confirmations === 1 ? "change" : "changes"} to confirm`,
      tone: "warn",
    });
  if (s.needs_review) items.push({ text: `${s.needs_review} to review`, tone: "warn" });
  if (s.filename_mismatches)
    items.push({
      text: `${s.filename_mismatches} misnamed ${s.filename_mismatches === 1 ? "file" : "files"}`,
      tone: "warn",
    });
  if (s.superseded) items.push({ text: `${s.superseded} superseded`, tone: "muted" });
  for (const f of s.different_formats ?? [])
    items.push({ text: `Different format: ${f.split(": ").slice(1).join(": ")}`, tone: "muted" });
  if (!items.length) {
    return <span className="text-sm text-graphite">{s.documents ? "None" : ""}</span>;
  }
  return (
    // One plain line per signal; only the ones that need someone are in the warning colour
    <span className="flex flex-col gap-0.5 text-sm leading-snug">
      {items.map((item) => (
        <span key={item.text} className={item.tone === "warn" ? "text-warning" : "text-graphite"}>
          {item.text}
        </span>
      ))}
    </span>
  );
}

/** The current time, refreshed every `everyMs`, for ages that keep moving. */
function useNow(everyMs: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), everyMs);
    return () => clearInterval(timer);
  }, [everyMs]);
  return now;
}

function Toolbar({
  items,
  view,
  query,
  sort,
  onView,
  onQuery,
  onSort,
}: {
  items: SubmissionSummary[];
  view: ViewKey;
  query: string;
  sort: SortKey;
  onView: (v: ViewKey) => void;
  onQuery: (q: string) => void;
  onSort: (k: SortKey) => void;
}) {
  const n = counts(items);
  const search = useRef<HTMLInputElement>(null);
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key !== "/" || e.metaKey || e.ctrlKey) return;
      if (e.target instanceof HTMLElement && e.target.closest("input, textarea, select")) return;
      e.preventDefault();
      search.current?.focus();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  return (
    <div className="flex flex-wrap items-center gap-3">
      <nav aria-label="Inbox views" className="flex flex-wrap gap-5">
        {VIEWS.map((v) => (
          <button
            key={v.key}
            type="button"
            aria-pressed={v.key === view}
            onClick={() => onView(v.key)}
            className={clsx(
              "inline-flex items-center gap-1.5 border-b-2 py-1.5 text-base",
              v.key === view
                ? "border-interstate text-ink"
                : "border-transparent text-graphite hover:text-ink",
            )}
          >
            {v.label}
            <span className="text-sm text-graphite tabular-nums">{n[v.key]}</span>
          </button>
        ))}
      </nav>
      <div className="ml-auto flex items-center gap-2">
        <label className="relative">
          <span className="sr-only">Search submissions</span>
          <Search
            size={14}
            aria-hidden="true"
            className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-graphite"
          />
          <input
            ref={search}
            type="search"
            defaultValue={query}
            onChange={(e) => onQuery(e.target.value)}
            placeholder="Search"
            className="h-9 w-56 rounded-[var(--radius-control)] border border-hairline bg-surface pr-8 pl-8 text-sm placeholder:text-graphite"
          />
          <span className="pointer-events-none absolute top-1/2 right-2 -translate-y-1/2">
            <Kbd>/</Kbd>
          </span>
        </label>
        <label className="flex items-center gap-1.5 text-sm text-graphite">
          Sort
          <select
            value={sort}
            onChange={(e) => onSort(e.target.value as SortKey)}
            className="h-9 rounded-[var(--radius-control)] border border-hairline bg-surface px-2 text-sm text-ink shadow-[var(--shadow-card)]"
          >
            <option value="received">Newest</option>
            <option value="age">Oldest</option>
            <option value="route">By route</option>
          </select>
        </label>
      </div>
    </div>
  );
}
