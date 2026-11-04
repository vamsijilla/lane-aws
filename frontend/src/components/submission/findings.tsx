"use client";

import clsx from "clsx";
import { Check, ChevronDown, FileSearch, RotateCcw, ShieldAlert, X } from "lucide-react";
import { useState } from "react";

import { useLens } from "@/components/evidence/lens";
import { refTarget } from "@/components/evidence/targets";
import { ApproveAnalysis, EarlierReview } from "@/components/submission/approve-analysis";
import { useSubmissionId } from "@/components/submission/shell";
import { Chip, ConfidenceBand, KindChip, SeverityMark } from "@/components/ui/chips";
import { Button, Empty, ErrorNote, Skeleton } from "@/components/ui/primitives";
import type { Schemas } from "@/lib/api";
import { modelName } from "@/lib/format";
import { useHashFlag } from "@/lib/hash-flag";
import { useAnalysis, useReviewFinding } from "@/lib/queries";
import { useSession } from "@/lib/session";

const SEVERITY = [
  ["high", "Could change whether or how the account is written"],
  ["medium", "Affects terms or needs an answer"],
  ["low", "Worth noting"],
] as const;

const CONFIDENCE_ORDER = ["high", "medium", "low"];
const KIND_ORDER = ["observation", "recommendation", "hypothesis"];
const rank = (order: string[], v: string) => {
  const i = order.indexOf(v);
  return i < 0 ? order.length : i;
};

// The card's left edge and chip say how much it matters
const SEVERITY_EDGE: Record<string, string> = {
  high: "border-l-stop",
  medium: "border-l-warning",
  low: "border-l-note",
};
const SEVERITY_CHIP: Record<string, "stop" | "warning" | "note"> = {
  high: "stop",
  medium: "warning",
  low: "note",
};

export function Findings() {
  const id = useSubmissionId();
  const analysis = useAnalysis(id);
  const [all, setAll] = useHashFlag("all-findings");
  if (analysis.isPending) return <Skeleton lines={12} />;
  if (analysis.error)
    return <ErrorNote title="Findings could not be loaded">{analysis.error.message}</ErrorNote>;
  const a = analysis.data;
  if (a.status === "not_run")
    return (
      <Empty icon={FileSearch} title="The analyst has not run yet">
        Rules and extracted facts are complete without it.
      </Empty>
    );

  if ((a.status === "awaiting_approval" || a.status === "running") && !a.findings.length) {
    return <ApproveAnalysis />;
  }

  const high = a.findings.filter((f) => f.severity === "high").length;
  const lower = a.findings.length - high;
  // High severity first; with none, there is nothing to put ahead, so show everything.
  const showAll = all || high === 0;

  return (
    <div className="space-y-5">
      <EarlierReview />
      <ApproveAnalysis />
      {a.message && a.status !== "running" && a.status !== "awaiting_approval" ? (
        <div className="rounded-[var(--radius-panel)] border border-unknown/30 bg-unknown-soft px-4 py-3 text-sm">
          {a.message}
        </div>
      ) : null}
      <p className="text-graphite">
        What an experienced underwriter would raise beyond the rules. Every finding cites the facts
        or rules it rests on; the system rejects any that do not. Hypotheses are labeled. Accepted
        recommendations join the agent request.
      </p>
      {SEVERITY.filter(([sev]) => showAll || sev === "high").map(([sev, meaning]) => {
        // Within a severity: the most confident first, then observations, recommendations,
        // hypotheses
        const list = a.findings
          .filter((f) => f.severity === sev)
          .sort(
            (x, y) =>
              rank(CONFIDENCE_ORDER, x.confidence) - rank(CONFIDENCE_ORDER, y.confidence) ||
              rank(KIND_ORDER, x.kind) - rank(KIND_ORDER, y.kind),
          );
        if (!list.length) return null;
        return (
          <section key={sev}>
            <h2 className="mb-2 flex items-baseline gap-2 font-display text-md font-semibold">
              <SeverityMark severity={sev} />
              <span className="text-sm font-normal text-graphite">{meaning}</span>
            </h2>
            <ul className="space-y-3">
              {list.map((f) => (
                <FindingCard key={f.id} f={f} refs={a.refs} />
              ))}
            </ul>
          </section>
        );
      })}
      {lower && high ? (
        <Button
          variant="quiet"
          icon={ChevronDown}
          onClick={() => setAll(!all)}
          aria-expanded={all}
          className={clsx(all && "[&_svg]:rotate-180")}
        >
          {all ? "Show high severity only" : `Show all ${a.findings.length} findings`}
        </Button>
      ) : null}
      {a.security_notes.length ? (
        <section className="rounded-[var(--radius-panel)] border border-warning/30 bg-warning-soft p-4">
          <p className="flex items-center gap-1.5 font-semibold text-warning">
            <ShieldAlert size={16} aria-hidden="true" /> Instructions found inside documents
          </p>
          <p className="mt-1 text-sm">Treated as data and ignored. Logged in the audit trail.</p>
          <ul className="mt-2 space-y-1 text-sm">
            {a.security_notes.map((n, i) => (
              <li key={i}>
                <q>{n.text}</q> <span className="text-graphite">in {n.where}</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {a.rejected.length ? (
        <details className="rounded-[var(--radius-panel)] border border-hairline px-4 py-3 text-sm">
          <summary className="cursor-pointer font-semibold">
            {a.rejected.length} findings rejected by the citation check
          </summary>
          <ul className="mt-2 space-y-1 text-graphite">
            {a.rejected.map((r, i) => (
              <li key={i}>
                {r.title}: {r.reason}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      <p className="text-xs text-graphite">
        {a.model ? modelName(a.model) : null}, prompt version {a.prompt_version}, {a.attempts}{" "}
        {a.attempts === 1 ? "attempt" : "attempts"}, ${a.cost_usd.toFixed(2)}. Personal identifiers
        were replaced before the model saw the submission.
      </p>
    </div>
  );
}

function FindingCard({
  f,
  refs,
}: {
  f: Schemas["FindingOut"];
  refs: Record<string, Schemas["RefOut"]>;
}) {
  const id = useSubmissionId();
  const lens = useLens();
  const session = useSession();
  const review = useReviewFinding(id);
  const [dismissing, setDismissing] = useState(false);
  const [reason, setReason] = useState("");
  const state = f.review_state;

  return (
    <li
      className={clsx(
        "min-w-0 rounded-[var(--radius-panel)] border border-l-[3px]",
        SEVERITY_EDGE[f.severity] ?? "border-l-note",
        state === "accepted"
          ? "border-guide/50"
          : state === "dismissed"
            ? "border-hairline opacity-70"
            : "border-hairline",
      )}
    >
      <div className="px-4 pt-3 pb-3">
        <div className="flex flex-wrap items-center gap-2">
          <Chip tone={SEVERITY_CHIP[f.severity] ?? "neutral"}>
            {f.severity.charAt(0).toUpperCase() + f.severity.slice(1)}
          </Chip>
          <KindChip kind={f.kind} />
          <ConfidenceBand band={f.confidence} label />
          {state === "accepted" ? (
            <Chip tone="guide" icon={Check}>
              Accepted
            </Chip>
          ) : null}
          {state === "dismissed" ? <Chip tone="neutral">Dismissed</Chip> : null}
        </div>
        <h3
          className={clsx(
            "mt-1.5 font-display text-md font-semibold",
            state === "dismissed" && "line-through decoration-graphite/50",
          )}
        >
          {f.title}
        </h3>
        <p className="mt-1 max-w-[80ch] leading-relaxed [overflow-wrap:anywhere]">{f.body}</p>
        <p className="mt-1.5 text-sm text-graphite [overflow-wrap:anywhere]">
          <span className="font-semibold">Rests on:</span> {f.basis}
        </p>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {f.citations.map((c) => {
            const ref: Schemas["RefOut"] | undefined = refs[c]; // a citation may not resolve
            const target = refTarget(ref, c, refs);
            const label = ref?.label ?? c;
            return (
              <button
                key={c}
                type="button"
                data-evidence=""
                disabled={!target}
                onClick={() => target && lens.open(target)}
                title={c}
                // The label reads; the id is on hover. Long labels wrap inside the card.
                className="max-w-full rounded-[var(--radius-cell)] border border-hairline bg-surface-sunk px-1.5 py-0.5 text-left text-xs [overflow-wrap:anywhere] hover:border-interstate hover:text-interstate disabled:cursor-default disabled:hover:border-hairline disabled:hover:text-inherit"
              >
                {label}
              </button>
            );
          })}
        </div>
        {f.review_reason ? (
          <p className="mt-2 text-sm text-graphite">Reason: {f.review_reason}</p>
        ) : null}
      </div>
      {session.can("review") ? (
        <div className="flex flex-wrap items-center gap-2 border-t border-hairline px-4 py-2">
          {state === "open" ? (
            dismissing ? (
              <form
                className="flex flex-1 items-center gap-2"
                onSubmit={(e) => {
                  e.preventDefault();
                  if (!reason.trim()) return;
                  review.mutate(
                    { findingId: f.id, state: "dismissed", reason },
                    { onSuccess: () => setDismissing(false) },
                  );
                }}
              >
                <input
                  autoFocus
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  placeholder="Why does this not apply?"
                  className="h-8 flex-1 rounded-[var(--radius-cell)] border border-hairline bg-surface px-2 text-sm"
                />
                <Button type="submit" size="sm" disabled={!reason.trim()} busy={review.isPending}>
                  Dismiss finding
                </Button>
                <Button size="sm" variant="quiet" onClick={() => setDismissing(false)}>
                  Cancel
                </Button>
              </form>
            ) : (
              <>
                <Button
                  size="sm"
                  icon={Check}
                  busy={review.isPending}
                  onClick={() => review.mutate({ findingId: f.id, state: "accepted" })}
                >
                  {f.kind === "recommendation" ? "Accept and add to agent request" : "Accept"}
                </Button>
                <Button size="sm" variant="quiet" icon={X} onClick={() => setDismissing(true)}>
                  Dismiss
                </Button>
              </>
            )
          ) : (
            <Button
              size="sm"
              variant="quiet"
              icon={RotateCcw}
              onClick={() => review.mutate({ findingId: f.id, state: "open" })}
            >
              Reopen
            </Button>
          )}
          {f.reviewed_by ? (
            <span className="ml-auto text-xs text-graphite">
              Reviewed by {f.reviewed_by.replace("user:", "")}
            </span>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}
