"use client";

import clsx from "clsx";
import { ArrowRight, Check, Circle, Copy, Mail, MailPlus } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useState } from "react";
import { toast } from "sonner";

import { EmailPanel, emailWhen, MATCHED } from "@/components/submission/email-panel";
import { Button, Panel } from "@/components/ui/primitives";
import type { Schemas } from "@/lib/api";
import { useSubmissionId } from "@/components/submission/shell";
import { formatDateTime, personName, plural } from "@/lib/format";
import { useConfirmRuleChange, useFollowUpDecision, useRequestSeniorReview } from "@/lib/queries";
import { useSession } from "@/lib/session";

/** This email is about another submission: the same files again, or a possible follow-up a
 * person attaches or keeps separate. Nothing is merged on a guess. */
export function RelationNote({ s }: { s: Schemas["SubmissionSummary"] }) {
  const router = useRouter();
  const decide = useFollowUpDecision(s.id);
  if (!s.related_to || (s.relation !== "same_files" && s.relation !== "possible_follow_up"))
    return null;
  const other = s.related_applicant ?? "an earlier submission";
  const link = (
    <Link
      href={`/submissions/${s.related_to}`}
      className="font-semibold text-interstate underline decoration-1 underline-offset-2"
    >
      {other}
    </Link>
  );
  if (s.relation === "same_files") {
    return (
      <Panel>
        <p className="flex items-start gap-2">
          <Copy size={16} className="mt-0.5 shrink-0 text-graphite" aria-hidden="true" />
          <span>
            Same files as submission {link}. Every attachment was already received there, so nothing
            was processed again.
          </span>
        </p>
      </Panel>
    );
  }
  const act = (attach: boolean) =>
    decide.mutate(attach, {
      onSuccess: (out) => {
        toast(attach ? `Attached to ${other}; running it again` : "Kept separate; processing it");
        router.push(`/submissions/${out.submission_id}`);
      },
      onError: (e) => toast.error(e.message),
    });
  return (
    <Panel>
      <div className="space-y-3">
        <p className="flex items-start gap-2">
          <MailPlus size={16} className="mt-0.5 shrink-0 text-warning" aria-hidden="true" />
          <span>
            <span className="font-semibold">Possible follow-up to {link}.</span> {s.relation_reason}
            . Lane does not merge on a guess: attach it, or keep it as a submission of its own.
          </span>
        </p>
        <div className="flex gap-2">
          <Button variant="primary" busy={decide.isPending} onClick={() => act(true)}>
            Attach
          </Button>
          <Button disabled={decide.isPending} onClick={() => act(false)}>
            Keep separate
          </Button>
        </div>
      </div>
    </Panel>
  );
}

const RULE_WORDS: Record<string, string> = {
  pass: "pass",
  fail: "fail",
  decline: "decline",
  refer: "refer",
  flag: "note",
  unknown: "can’t check yet",
  "n/a": "not applicable",
};

function outcomeWords(outcome: string | null | undefined, potential: boolean): string {
  if (potential) return "potential decline";
  return RULE_WORDS[outcome ?? ""] ?? outcome ?? "new";
}

function routeWords(r: Schemas["RouteLine"]): string {
  const head = (r.headline ?? "No route").replace(/ underwriting$/, "");
  return r.potential_decline ? `${head}, potential decline` : head;
}

/** What the agent's latest reply changed: the route, each rule outcome (a move away from
 * decline waits for an underwriter), and the questions it answered, by group and item. */
export function Amendment({
  a,
  reviewRequested = false,
}: {
  a: Schemas["AmendmentOut"];
  reviewRequested?: boolean;
}) {
  const id = useSubmissionId();
  const confirm = useConfirmRuleChange(id);
  const ask = useRequestSeniorReview(id);
  // Clearing a decline is four-eyes: a senior underwriter confirms (the API enforces it)
  const mayConfirm = useSession().mayDecide("confirm_cleared_decline");
  const basis =
    a.basis === "sent"
      ? `the request marked sent by ${personName(a.basis_by)}${a.basis_at ? ` on ${formatDateTime(a.basis_at)}` : ""}`
      : `the last draft${a.basis_version ? ` (version ${a.basis_version})` : ""}; it was never marked sent`;
  if (a.in_progress)
    return (
      <Panel title="Amended by email from the agent" description={a.email_subject ?? undefined}>
        <p className="text-sm text-graphite" role="status">
          Processing the agent&rsquo;s reply&hellip;
        </p>
      </Panel>
    );
  return (
    <Panel title="Amended by email from the agent" description={a.email_subject ?? undefined}>
      <div className="space-y-4 text-sm">
        {a.route_before ? (
          <p className="flex flex-wrap items-center gap-x-2">
            <span className="text-graphite">Route</span>
            <span>{routeWords(a.route_before)}</span>
            <ArrowRight size={12} aria-hidden="true" />
            <span className="font-semibold">{routeWords(a.route_after)}</span>
            {a.route_pending ? (
              <span className="text-warning">pending underwriter confirmation</span>
            ) : null}
          </p>
        ) : null}

        {a.rule_changes.length ? (
          <ul className="space-y-1.5">
            {a.rule_changes.map((c) => (
              <li key={c.rule_id} className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span className="font-mono text-xs text-graphite">{c.rule_id}</span>
                <span>{c.title}:</span>
                <span className="text-graphite">
                  {outcomeWords(c.before, c.was_potential_decline)}
                </span>
                <ArrowRight size={12} aria-hidden="true" />
                <span className="font-semibold">{outcomeWords(c.after, c.potential_decline)}</span>
                {c.confirmation === "pending" && c.confirmation_id ? (
                  <>
                    <span className="text-warning">pending underwriter confirmation</span>
                    {mayConfirm ? (
                      <Button
                        size="sm"
                        busy={confirm.isPending}
                        onClick={() =>
                          confirm.mutate(c.confirmation_id!, {
                            onSuccess: () => toast(`${c.rule_id} confirmed`),
                            onError: (e) => toast.error(e.message),
                          })
                        }
                      >
                        Confirm
                      </Button>
                    ) : (
                      <>
                        <span className="text-graphite">Needs senior underwriter authority.</span>
                        {reviewRequested ? (
                          <span className="text-graphite">
                            A senior underwriter has been asked.
                          </span>
                        ) : (
                          <Button
                            size="sm"
                            busy={ask.isPending}
                            onClick={() =>
                              ask.mutate("confirm", {
                                onSuccess: () => toast("Senior review requested"),
                                onError: (e) => toast.error(e.message),
                              })
                            }
                          >
                            Request review
                          </Button>
                        )}
                      </>
                    )}
                  </>
                ) : c.confirmation === "confirmed" ? (
                  <span className="text-graphite">confirmed by {personName(c.confirmed_by)}</span>
                ) : null}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-graphite">No rule outcome changed.</p>
        )}

        <div className="space-y-3">
          <p>
            <span className="font-semibold">
              Answered {a.answered} of {a.requested} points
            </span>{" "}
            <span className="text-graphite">measured against {basis}.</span>
          </p>
          {a.groups.map((g) => (
            <section key={g.group} className="space-y-1.5">
              <h4 className="font-semibold">
                {g.label} <Progress answered={g.answered} of={g.requested} />
              </h4>
              <ul className="space-y-1.5 pl-1">
                {g.items.map((it) => (
                  <ItemPoints key={it.item} it={it} />
                ))}
              </ul>
            </section>
          ))}
          {a.answered_not_requested.length ? (
            <section className="space-y-1">
              <h4 className="font-semibold">Answered, not requested</h4>
              <Lines rows={a.answered_not_requested} />
            </section>
          ) : null}
          <section className="space-y-1">
            {a.new.length ? (
              <>
                <h4 className="font-semibold">
                  {plural(a.new.length, "new question")} from the reply
                </h4>
                <Lines rows={a.new} />
              </>
            ) : (
              <p className="text-graphite">No new questions from the reply.</p>
            )}
          </section>
        </div>
      </div>
    </Panel>
  );
}

function ItemPoints({ it }: { it: Schemas["ItemPointsOut"] }) {
  return (
    <li>
      <details open={it.answered > 0}>
        <summary className="cursor-pointer">
          {it.item} <Progress answered={it.answered} of={it.total} />
        </summary>
        <ul className="mt-1 space-y-0.5 pl-5">
          {it.points.map((p) => (
            <li key={p.lines.join("|")} className="flex gap-2">
              <span
                className={p.state === "answered" ? "text-guide" : "text-graphite"}
                aria-label={p.state === "answered" ? "Answered" : "Still open"}
              >
                {p.state === "answered" ? <Check size={13} /> : <Circle size={11} />}
              </span>
              <span className={p.state === "answered" ? "" : "text-graphite"}>
                {sentences(p.lines)}
              </span>
            </li>
          ))}
        </ul>
      </details>
    </li>
  );
}

/** How far an item or group got, as a small label beside its name: "1 of 1 answered". */
function Progress({ answered, of }: { answered: number; of: number }) {
  return (
    <span
      className={clsx(
        "ml-1 text-xs font-normal",
        answered === of && of > 0 ? "text-guide" : "text-graphite",
      )}
    >
      {answered} of {of} answered
    </span>
  );
}

/** Request lines read as sentences: each ends with its own punctuation before the next. */
function sentences(lines: string[]): string {
  return lines
    .map((l) => l.trim())
    .filter(Boolean)
    .map((l) => (/[.?!:]$/.test(l) ? l : `${l}.`))
    .join(" ");
}

function Lines({ rows }: { rows: Schemas["LinesOut"][] }) {
  return (
    <ul className="list-disc space-y-0.5 pl-5 text-graphite">
      {rows.map((r) => (
        <li key={`${r.item}|${r.lines.join("|")}`}>
          {r.item}: {sentences(r.lines)}
        </li>
      ))}
    </ul>
  );
}

/** Every email that went into the submission. A row opens the email beside the page; the run
 * it started is on Audit. */
export function EmailTrail({ emails }: { emails: Schemas["EmailOut"][] }) {
  const id = useSubmissionId();
  const [open, setOpen] = useState<string | null>(null);
  const close = useCallback(() => setOpen(null), []);
  return (
    <Panel title="Emails" description="The original and each reply attached to it">
      <ol className="-mx-2 text-sm">
        {emails.map((e) => (
          <li
            key={e.id}
            onClick={() => setOpen(e.id)}
            className={clsx(
              "flex cursor-pointer items-start gap-2 rounded-[var(--radius-cell)] px-2 py-1.5 hover:bg-surface-sunk",
              open === e.id && "bg-surface-sunk",
            )}
          >
            <Mail size={14} className="mt-0.5 shrink-0 text-graphite" aria-hidden="true" />
            <button
              type="button"
              aria-haspopup="dialog"
              aria-expanded={open === e.id}
              className="min-w-0 flex-1 text-left focus-visible:outline-2 focus-visible:outline-interstate"
            >
              <span className="block truncate font-semibold" title={e.subject}>
                {e.subject || "(no subject)"}
              </span>
              <span className="block text-graphite">
                {e.role === "original"
                  ? `Original from ${e.sender}, ${emailWhen(e.received_at)}`
                  : `Reply, ${(MATCHED[e.how ?? ""] ?? "attached").replace(/^M/, "m").replace(/^A/, "a")}, ${emailWhen(e.received_at)}`}
              </span>
            </button>
            {e.run_id ? (
              <Link
                href={`/submissions/${id}/audit`}
                onClick={(ev) => ev.stopPropagation()}
                className="mt-0.5 shrink-0 text-xs text-interstate hover:underline"
              >
                View run
              </Link>
            ) : null}
          </li>
        ))}
      </ol>
      <EmailPanel submissionId={id} emailId={open} onClose={close} />
    </Panel>
  );
}
