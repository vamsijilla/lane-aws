"use client";

import { Gavel } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { useSession } from "@/lib/session";
import { Button, Panel } from "@/components/ui/primitives";
import type { Schemas } from "@/lib/api";
import { formatDateTime, personName } from "@/lib/format";
import { useRecordDecision, useRequestSeniorReview } from "@/lib/queries";

export const DECISION_LABELS: Record<string, string> = {
  decline: "Decline",
  refer_back: "Refer back to agent",
  quote: "Proceed to quote",
};

/** The underwriter decides, with a reason and any conditions. Lane only recommends; its
 * recommendation stays on the page beside this. */
export function DecisionPanel({
  s,
  recommendation,
}: {
  s: Schemas["SubmissionSummary"];
  recommendation: string;
}) {
  const session = useSession();
  const record = useRecordDecision(s.id);
  const [choice, setChoice] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [conditions, setConditions] = useState("");
  const canDecide = session.can("review");
  // Quoting while Lane's route is a decline or potential decline, or a cleared decline awaits
  // confirmation, needs senior authority; the API enforces the same rule
  const atRisk = s.potential_decline || s.recommendation === "decline";
  const blocked = (key: string) =>
    key === "quote" && atRisk ? !session.mayDecide("quote_at_risk") : !session.mayDecide(key);

  return (
    <Panel
      boxed
      title="Your decision"
      description={`Lane recommends: ${recommendation}. You decide; both are kept.`}
    >
      <div className="space-y-3 text-sm">
        {s.decision ? (
          <div className="rounded-[var(--radius-cell)] bg-surface-sunk px-3 py-2">
            <p className="flex items-center gap-2 font-semibold">
              <Gavel size={14} aria-hidden="true" />
              {DECISION_LABELS[s.decision] ?? s.decision}
            </p>
            <p className="text-graphite">
              {personName(s.decided_by)}, {formatDateTime(s.decided_at)}: {s.decision_reason}
            </p>
            {s.decision_conditions?.length ? (
              <ul className="mt-1 list-disc pl-5 text-graphite">
                {s.decision_conditions.map((c) => (
                  <li key={c}>Condition: {c}</li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : (
          <p className="text-graphite">No decision recorded yet.</p>
        )}
        {s.senior_review ? (
          <p className="text-warning">
            Senior review requested by {personName(s.senior_review.by)},{" "}
            {formatDateTime(s.senior_review.at)}
          </p>
        ) : null}
        {canDecide ? (
          <>
            <div className="flex flex-wrap gap-2" role="group" aria-label="Decision">
              {Object.entries(DECISION_LABELS).map(([key, label]) => (
                <Button
                  key={key}
                  size="sm"
                  variant={choice === key ? "primary" : "secondary"}
                  aria-pressed={choice === key}
                  onClick={() => setChoice(key)}
                >
                  {label}
                </Button>
              ))}
            </div>
            {choice && blocked(choice) ? (
              <SeniorNeeded
                submissionId={s.id}
                action="quote"
                requested={Boolean(s.senior_review)}
                why="Lane's route is a potential decline, so a senior underwriter decides on quoting."
              />
            ) : choice ? (
              <div className="space-y-2">
                <label className="block">
                  <span className="text-graphite">Reason (required)</span>
                  <textarea
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                    rows={2}
                    className="mt-1 w-full rounded-[var(--radius-cell)] border border-hairline bg-surface px-2 py-1.5"
                  />
                </label>
                <label className="block">
                  <span className="text-graphite">Conditions, one per line (optional)</span>
                  <textarea
                    value={conditions}
                    onChange={(e) => setConditions(e.target.value)}
                    rows={2}
                    placeholder="Named driver exclusion signed by the insured"
                    className="mt-1 w-full rounded-[var(--radius-cell)] border border-hairline bg-surface px-2 py-1.5"
                  />
                </label>
                <Button
                  variant="primary"
                  disabled={!reason.trim()}
                  busy={record.isPending}
                  onClick={() =>
                    record.mutate(
                      {
                        decision: choice,
                        reason,
                        conditions: conditions.split("\n").filter((c) => c.trim()),
                      },
                      {
                        onSuccess: () => {
                          toast(`Recorded: ${DECISION_LABELS[choice]}`);
                          setChoice(null);
                          setReason("");
                          setConditions("");
                        },
                        onError: (e) => toast.error(e.message),
                      },
                    )
                  }
                >
                  Record decision
                </Button>
              </div>
            ) : null}
          </>
        ) : null}
      </div>
    </Panel>
  );
}

/** What a person without the authority sees instead of the action, and a way to ask for it. */
export function SeniorNeeded({
  submissionId,
  action,
  requested,
  why,
}: {
  submissionId: string;
  action: "quote" | "confirm";
  requested: boolean;
  why: string;
}) {
  const ask = useRequestSeniorReview(submissionId);
  return (
    <div className="space-y-2 rounded-[var(--radius-cell)] border border-hairline px-3 py-2">
      <p>
        <span className="font-semibold">Needs senior underwriter authority.</span>{" "}
        <span className="text-graphite">{why}</span>
      </p>
      {requested ? (
        <p className="text-graphite">A senior underwriter has been asked.</p>
      ) : (
        <Button
          size="sm"
          busy={ask.isPending}
          onClick={() =>
            ask.mutate(action, {
              onSuccess: () => toast("Senior review requested"),
              onError: (e) => toast.error(e.message),
            })
          }
        >
          Request review
        </Button>
      )}
    </div>
  );
}
