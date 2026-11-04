import type { SubmissionSummary } from "@/lib/api";

/**
 * The inbox as a work queue: views an underwriter works from, the age of each submission against
 * a turnaround target, search and sort. Pure functions, so the rules are tested apart from the UI.
 */

/** Hours from receipt to a first underwriting decision (a setting, NEXT_PUBLIC_LANE_TURNAROUND_HOURS). */
export const TURNAROUND_HOURS = Number(process.env.NEXT_PUBLIC_LANE_TURNAROUND_HOURS ?? 4) || 4;

export type ViewKey = "underwriter" | "decline" | "ready" | "processing" | "all";

const processing = (s: SubmissionSummary) =>
  s.status === "processing" || s.status === "received" || s.latest_run?.status === "running";

export const VIEWS: { key: ViewKey; label: string; matches: (s: SubmissionSummary) => boolean }[] =
  [
    {
      key: "underwriter",
      label: "Needs an underwriter",
      matches: (s) => !processing(s) && (s.recommendation === "refer" || s.needs_review > 0),
    },
    {
      key: "decline",
      label: "Potential decline",
      matches: (s) => !processing(s) && (s.potential_decline || s.recommendation === "decline"),
    },
    {
      key: "ready",
      label: "Ready to quote",
      matches: (s) => !processing(s) && s.recommendation === "ready",
    },
    { key: "processing", label: "Processing", matches: processing },
    { key: "all", label: "All", matches: () => true },
  ];

export function viewOf(key: string | null): ViewKey {
  return VIEWS.some((v) => v.key === key) ? (key as ViewKey) : "all";
}

export type SortKey = "received" | "age" | "route";

const ROUTE_RANK: Record<string, number> = { decline: 0, refer: 1, ready: 2 };

export function arrange(
  items: SubmissionSummary[],
  { view, query, sort }: { view: ViewKey; query: string; sort: SortKey },
): SubmissionSummary[] {
  const match = VIEWS.find((v) => v.key === view)?.matches ?? (() => true);
  const q = query.trim().toLowerCase();
  const found = items.filter(
    (s) =>
      match(s) &&
      (!q ||
        [s.subject, s.sender_email, s.sender_name ?? "", s.recommendation_headline ?? ""]
          .join(" ")
          .toLowerCase()
          .includes(q)),
  );
  const received = (s: SubmissionSummary) => Date.parse(s.received_at);
  return found.sort((a, b) => {
    if (sort === "age") return received(a) - received(b); // oldest first: the most overdue
    if (sort === "route") {
      const rank = (s: SubmissionSummary) =>
        (ROUTE_RANK[s.recommendation ?? ""] ?? 3) - (s.potential_decline ? 0.5 : 0);
      return rank(a) - rank(b) || received(a) - received(b);
    }
    return received(b) - received(a); // newest first
  });
}

export function counts(items: SubmissionSummary[]): Record<ViewKey, number> {
  return Object.fromEntries(VIEWS.map((v) => [v.key, items.filter(v.matches).length])) as Record<
    ViewKey,
    number
  >;
}

/** "2h 10m", "3 days": compact, for a column. */
export function duration(ms: number): string {
  const minutes = Math.max(0, Math.round(ms / 60_000));
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return minutes % 60 ? `${hours}h ${minutes % 60}m` : `${hours}h`;
  return `${Math.round(hours / 24)} days`;
}

/** How old a submission is and where it stands against the turnaround target. */
export function age(
  receivedAt: string,
  now: number,
  hours = TURNAROUND_HOURS,
): { age: string; left: string; state: "ok" | "soon" | "overdue" } {
  const elapsed = now - Date.parse(receivedAt);
  const remaining = hours * 3_600_000 - elapsed;
  return {
    age: duration(elapsed),
    left: remaining >= 0 ? `Due in ${duration(remaining)}` : `Was due ${duration(-remaining)} ago`,
    state: remaining < 0 ? "overdue" : remaining < 3_600_000 ? "soon" : "ok",
  };
}
