import type { Schemas } from "@/lib/api";

type Event = Schemas["AuditEventOut"];

/** Events worth seeing first: what people did, security, and the outcomes of runs. */
const KEY_ACTIONS = new Set([
  "email.received",
  "submission.decided",
  "submission.analyzed",
  "run.replayed",
  "run.interrupted",
  "attachment.unreadable",
  "attachment.damaged",
]);

export function isKey(e: Event): boolean {
  return e.kind === "human" || e.kind === "security" || KEY_ACTIONS.has(e.action);
}

export type TimelineItem =
  { type: "event"; event: Event } | { type: "group"; id: string; events: Event[]; summary: string };

const FAMILY: [prefix: string, one: string, many: string][] = [
  ["document.classified", "document recognised", "documents recognised"],
  ["document.extracted", "extraction", "extractions"],
  ["attachment.", "file event", "file events"],
  ["step.", "step", "steps"],
  ["run.", "run marker", "run markers"],
  ["submission.resolved", "reconciliation", "reconciliations"],
  ["agent_request.", "agent request update", "agent request updates"],
];

/** "24 documents recognised, 24 extractions, 7 steps and 12 more". */
export function describeGroup(events: Event[]): string {
  const counts = new Map<string, number>();
  let other = 0;
  for (const e of events) {
    const f = FAMILY.find(([prefix]) => e.action.startsWith(prefix));
    if (f) counts.set(f[0], (counts.get(f[0]) ?? 0) + 1);
    else other += 1;
  }
  const parts = FAMILY.filter(([p]) => counts.has(p))
    .map(([p, one, many]) => [counts.get(p) ?? 0, one, many] as const)
    .sort((a, b) => b[0] - a[0]);
  const shown = parts.slice(0, 3).map(([n, one, many]) => `${n} ${n === 1 ? one : many}`);
  const rest = other + parts.slice(3).reduce((a, [n]) => a + n, 0);
  return shown.join(", ") + (rest ? `${shown.length ? " and " : ""}${rest} more` : "");
}

/** Key events stand alone; each run of routine events between them folds into one group. */
export function foldRoutine(events: Event[]): TimelineItem[] {
  const out: TimelineItem[] = [];
  let routine: Event[] = [];
  const flush = () => {
    if (!routine.length) return;
    out.push(
      routine.length === 1
        ? { type: "event", event: routine[0] }
        : {
            type: "group",
            id: `${routine[0].seq}-${routine[routine.length - 1].seq}`,
            events: routine,
            summary: describeGroup(routine),
          },
    );
    routine = [];
  };
  for (const e of events) {
    if (isKey(e)) {
      flush();
      out.push({ type: "event", event: e });
    } else routine.push(e);
  }
  flush();
  return out;
}
