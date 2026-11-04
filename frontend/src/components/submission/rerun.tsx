"use client";

import { ArrowRight, X } from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { createContext, useCallback, useContext, useMemo, useState } from "react";

import { toast } from "sonner";

import { OutcomeChip } from "@/components/ui/chips";
import type { Schemas } from "@/lib/api";

/**
 * A rerun after a person's decision, and what the decision was ("Owner drives resolved").
 * `undo` reverses the decision (it reruns the rules again); `undone` names that reversal.
 */
export type Rerun = Schemas["RerunOut"] & {
  what: string;
  undo?: () => Promise<Schemas["RerunOut"]>;
  undone?: string;
};

/** One line on what the rerun changed, for the toast and the panel. */
export function rerunSummary(r: Schemas["RerunOut"]): string {
  const changed = r.changes.length
    ? `${r.changes.length} ${r.changes.length === 1 ? "outcome" : "outcomes"} changed.`
    : "No rule outcome changed.";
  const decline = r.potential_after
    ? ", still a potential decline"
    : r.potential_before
      ? ", no longer a potential decline"
      : "";
  return `${changed} ${r.headline_after}${decline}.`;
}

type RerunState = {
  last: Rerun | null;
  publish: (r: Rerun) => void;
  clear: () => void;
};

const RerunContext = createContext<RerunState | null>(null);

/**
 * The latest rerun for this submission. Resolving a conflict, reopening it, correcting a value
 * or undoing a correction all publish here, so the diff shows on whichever tab the person is on
 * and the rules tab can mark what changed.
 */
export function RerunProvider({ children }: { children: React.ReactNode }) {
  const [last, setLast] = useState<Rerun | null>(null);
  const clear = useCallback(() => setLast(null), []);
  // Every decision confirms itself in a toast; one that can be reversed offers Undo for 8 s
  const publish = useCallback((r: Rerun) => {
    setLast(r);
    const undo = r.undo;
    toast(r.what, {
      description: rerunSummary(r),
      duration: undo ? 8000 : 4000,
      action: undo
        ? {
            label: "Undo",
            onClick: () =>
              void undo()
                .then((back) => {
                  const what = r.undone ?? `${r.what}, undone`;
                  setLast({ ...back, what });
                  toast(what, { description: rerunSummary(back) });
                })
                .catch(() => toast.error("Could not undo. Reopen it from the conflict.")),
          }
        : undefined,
    });
  }, []);
  const value = useMemo(() => ({ last, publish, clear }), [last, publish, clear]);
  return <RerunContext.Provider value={value}>{children}</RerunContext.Provider>;
}

export function useRerun(): RerunState {
  const state = useContext(RerunContext);
  if (!state) throw new Error("useRerun outside RerunProvider");
  return state;
}

/** The last rerun's diff, shown at the top of the submission until dismissed. */
export function LastRerun() {
  const { last, clear } = useRerun();
  return (
    <AnimatePresence>
      {last ? <RerunPanel key={last.run_id} r={last} onClose={clear} /> : null}
    </AnimatePresence>
  );
}

/** What the rerun changed: each rule's outcome before and after, and the recommendation. */
export function RerunPanel({ r, onClose }: { r: Rerun; onClose: () => void }) {
  const reduced = useReducedMotion();
  return (
    <motion.section
      initial={reduced ? { opacity: 0 } : { opacity: 0, y: -8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0 }}
      transition={{ duration: reduced ? 0 : 0.25 }}
      aria-live="polite"
      className="mb-4 rounded-[var(--radius-panel)] border border-interstate/40 bg-interstate-soft/50 px-4 py-3"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="font-semibold">
            Rules ran again after {r.what.charAt(0).toLowerCase() + r.what.slice(1)}
          </p>
          <p className="text-sm text-graphite">{rerunSummary(r)}</p>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Dismiss"
          className="rounded-[4px] p-1 text-graphite hover:text-ink"
        >
          <X size={16} />
        </button>
      </div>
      {r.changes.length ? (
        <ul className="mt-2 space-y-1.5">
          {r.changes.map((ch, i) => (
            <li key={ch.rule_id} className="flex flex-wrap items-center gap-2 text-sm">
              <span className="w-16 font-mono text-graphite">{ch.rule_id}</span>
              <span className="min-w-48 font-semibold">{ch.title}</span>
              <OutcomeChip outcome={ch.before} potential={ch.potential_before} />
              <ArrowRight size={14} className="text-graphite" aria-hidden="true" />
              <motion.span
                initial={reduced ? false : { scale: 0.6, opacity: 0 }}
                animate={{ scale: 1, opacity: 1 }}
                transition={
                  reduced
                    ? { duration: 0 }
                    : { delay: 0.25 + i * 0.08, type: "spring", stiffness: 400, damping: 22 }
                }
              >
                <OutcomeChip outcome={ch.after} potential={ch.potential_after} />
              </motion.span>
            </li>
          ))}
        </ul>
      ) : null}
    </motion.section>
  );
}
