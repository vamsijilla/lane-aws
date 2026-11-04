"use client";

import { useSubmissionId } from "@/components/submission/shell";
import { Button } from "@/components/ui/primitives";
import { useAnalysis, useApproveAnalysis } from "@/lib/queries";
import { useSession } from "@/lib/session";

/**
 * Paid model runs need a person's approval (assumption A16). Shown wherever the analysis would
 * appear while it waits; one approval covers one run.
 */
/** A review written before the agent's latest reply: shown as earlier, never as current. */
export function EarlierReview() {
  const id = useSubmissionId();
  const analysis = useAnalysis(id);
  if (!analysis.data?.stale) return null;
  return (
    <p className="mb-3 rounded-[var(--radius-cell)] bg-warning-soft px-3 py-2 text-sm">
      <span className="font-semibold">From an earlier review.</span> It was written before the
      agent&apos;s latest reply, so some of what it asks may already be answered. Rerun the expert
      review to bring it up to date.
    </p>
  );
}

export function ApproveAnalysis() {
  const id = useSubmissionId();
  const analysis = useAnalysis(id);
  const approve = useApproveAnalysis(id);
  const session = useSession();
  const a = analysis.data;
  if (!a || (a.status !== "awaiting_approval" && a.status !== "running")) return null;
  const running = a.status === "running" || approve.isPending;
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-[var(--radius-panel)] border border-interstate/30 bg-interstate-soft/50 px-4 py-3">
      <p className="min-w-60 flex-1 text-sm">{a.message}</p>
      {session.can("approve_spend") ? (
        <Button
          variant="primary"
          busy={running}
          onClick={() => approve.mutate()}
          disabled={running}
        >
          {running
            ? "Running expert review"
            : `${a.stale ? "Rerun" : "Run"} expert review (about $${(a.estimate_usd ?? 0).toFixed(2)})`}
        </Button>
      ) : (
        <span className="text-sm text-graphite">An underwriter approves model spend.</span>
      )}
    </div>
  );
}
