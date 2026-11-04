"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Archive, Mail } from "lucide-react";
import { AnimatePresence, LayoutGroup, motion, useReducedMotion } from "motion/react";
import { useEffect } from "react";

import { Counter, RuleMark, Steps, Tray } from "@/components/submission/live-intake/marks";
import { Tile } from "@/components/submission/live-intake/tile";
import { useIntakeStream } from "@/components/submission/live-intake/use-intake-stream";
import { useSubmissionId } from "@/components/submission/shell";
import { RouteSign } from "@/components/ui/route-sign";
import { keys } from "@/lib/queries";

/*
 * Live intake: the one orchestrated moment. Every movement is driven by a real event from the
 * pipeline (Server-Sent Events), never by a timer. With reduced motion the same sequence plays as
 * plain state changes, and a polite live region reads the progress.
 */

export function LiveIntake() {
  const id = useSubmissionId();
  const qc = useQueryClient();
  const reduced = useReducedMotion();
  const s = useIntakeStream(id);

  useEffect(() => {
    if (!s.finished) return;
    // The route sign has landed; ease into the brief once the data is fresh.
    const t = setTimeout(() => void qc.invalidateQueries({ queryKey: keys.submission(id) }), 1600);
    return () => clearTimeout(t);
  }, [s.finished, id, qc]);

  const originals = s.attachments.filter(
    (a) => a.status !== "duplicate" && a.status !== "expanded",
  );
  const duplicates = s.attachments.filter((a) => a.status === "duplicate").length;
  const known = Object.values(s.docs)
    .flat()
    .filter((d) => d.known).length;
  const ease = reduced ? { duration: 0 } : { duration: 0.32, ease: [0.2, 0.8, 0.2, 1] as const };

  return (
    <div className="space-y-6">
      <p className="sr-only" aria-live="polite">
        {s.last}
      </p>
      <motion.div
        initial={reduced ? false : { opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={ease}
        className="flex items-start gap-3 rounded-[var(--radius-panel)] border border-hairline px-4 py-3"
      >
        <Mail size={18} className="mt-0.5 text-interstate" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <p className="truncate font-semibold">{s.subject || "Waiting for the email"}</p>
          <p className="text-sm text-graphite">{s.from}</p>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1.5">
          <Steps order={s.stepOrder} steps={s.steps} />
          <p className="text-xs text-graphite">{s.finished ? "Done" : s.last}</p>
        </div>
      </motion.div>

      <section aria-label="Files">
        <div className="mb-2 flex items-baseline justify-between">
          <h2 className="font-display text-md font-semibold">
            Attachments
            {known ? (
              <span className="ml-2 text-sm font-normal text-graphite">
                {known} {known === 1 ? "document" : "documents"} read by a known layout, no model
                call
              </span>
            ) : null}
          </h2>
          <AnimatePresence>
            {duplicates ? (
              <motion.p
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                className="text-sm text-graphite"
              >
                <Archive size={13} className="mr-1 inline" aria-hidden="true" />
                {duplicates} duplicates merged
              </motion.p>
            ) : null}
          </AnimatePresence>
        </div>
        <LayoutGroup>
          <ul className="grid grid-cols-[repeat(auto-fill,minmax(15rem,1fr))] gap-2">
            {originals.map((a, i) => (
              <Tile
                key={a.id}
                a={a}
                index={i}
                pages={s.pages[a.id]}
                docs={s.docs[a.id] ?? []}
                reduced={Boolean(reduced)}
                running={s.steps.triage === "running"}
              />
            ))}
          </ul>
        </LayoutGroup>
      </section>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-4">
        <Counter label="Facts extracted" value={s.facts} reduced={Boolean(reduced)} />
        <Tray label="Conflicts" items={s.conflicts} tone="warning" />
        <Tray label="Missing" items={s.missing} tone="dashed" />
        <div className="rounded-[var(--radius-panel)] border border-hairline p-3">
          <p className="mb-2 text-sm font-semibold text-graphite">Guideline rules</p>
          <ul className="flex flex-wrap gap-1">
            <AnimatePresence>
              {s.rules.map((r, i) => (
                <motion.li
                  key={r.id}
                  initial={reduced ? false : { opacity: 0, scale: 0.85 }}
                  animate={{ opacity: 1, scale: 1 }}
                  transition={{ ...ease, delay: reduced ? 0 : Math.min(i * 0.025, 0.6) }}
                  title={r.id}
                >
                  <RuleMark id={r.id} outcome={r.outcome} potential={r.potential} />
                </motion.li>
              ))}
            </AnimatePresence>
          </ul>
        </div>
      </div>

      <AnimatePresence>
        {s.decision ? (
          <motion.div
            initial={reduced ? { opacity: 0 } : { opacity: 0, y: -24, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            transition={reduced ? { duration: 0 } : { type: "spring", stiffness: 260, damping: 26 }}
          >
            <RouteSign
              outcome={s.decision.outcome}
              headline={s.decision.headline}
              lines={[s.decision.potential ? "Potential decline" : null]}
            />
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
}
