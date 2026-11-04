"use client";

import clsx from "clsx";
import { Fragment } from "react";
import { ShieldAlert } from "lucide-react";
import Link from "next/link";

import { EvidenceLink, Footnote } from "@/components/evidence/evidence-link";
import { ApproveAnalysis, EarlierReview } from "@/components/submission/approve-analysis";
import { DecisionPanel } from "@/components/submission/decision";
import { Amendment, EmailTrail, RelationNote } from "@/components/submission/follow-up";
import { LiveIntake } from "@/components/submission/live-intake";
import { useRerun } from "@/components/submission/rerun";
import { RuleHint, RuleRef } from "@/components/submission/rule-ref";
import { useSubmissionId } from "@/components/submission/shell";
import { Hint } from "@/components/ui/hint";
import { ErrorNote, Panel, Skeleton } from "@/components/ui/primitives";
import { RouteSign } from "@/components/ui/route-sign";
import type { Schemas } from "@/lib/api";
import { clusterLabel, clusters, clusterTarget, paragraphs } from "@/lib/footnotes";
import { modelName, plural } from "@/lib/format";
import { statusHint } from "@/lib/glossary";
import { useAnalysis, useOverview, useRules, useRuns } from "@/lib/queries";
import { groupCounts, kindOf, NEEDS, SEVERITY, waitingGroups } from "@/lib/rule-groups";

export function OverviewBrief() {
  const id = useSubmissionId();
  const overview = useOverview(id);
  if (overview.isPending) return <Skeleton lines={10} />;
  if (overview.error)
    return <ErrorNote title="The brief could not be loaded">{overview.error.message}</ErrorNote>;
  const o = overview.data;
  const rel = o.submission.relation;
  if (rel === "same_files" || rel === "possible_follow_up")
    return <RelationNote s={o.submission} />;
  // A reply being processed keeps the Brief; its panel says so until the run finishes
  const live =
    (o.submission.status === "processing" || o.submission.latest_run?.status === "running") &&
    !o.amendment?.in_progress;
  if (live || !o.route) return <LiveIntake />;

  const needed = o.actions.items_by_group.before_quote ?? 0;
  // A07: checks no submission can answer (SAFER, premium); "Ready" never means they are done
  const atQuote = o.route.checks_at_quote;
  const declines = o.blockers.filter((b) => b.kind === "potential_decline");
  return (
    <div className="grid grid-cols-1 gap-x-12 gap-y-8 xl:grid-cols-[minmax(0,1fr)_20rem]">
      <div className="min-w-0 space-y-8">
        <div>
          <RouteSign
            outcome={o.route.outcome}
            headline={o.route.headline}
            alarm={declines.length > 0}
            sentence={verdictSentence(
              declines.map((d) => d.headline ?? d.title),
              needed,
            )}
            lines={[
              o.route.pending?.length
                ? `Pending underwriter confirmation (until then, still a potential decline): ${o.route.pending.join("; ")}`
                : null,
              atQuote.length
                ? `${plural(atQuote.length, "check")} at quote: ${atQuote.join(", ")}.`
                : null,
            ]}
          />
        </div>
        <DecisionPanel s={o.submission} recommendation={o.route.headline} />
        <RunHealth />
        {o.amendment ? (
          <div id="agent-reply" className="scroll-mt-40">
            <Amendment a={o.amendment} reviewRequested={Boolean(o.submission.senior_review)} />
          </div>
        ) : null}
        {o.security_notes.length ? <SecurityNotes notes={o.security_notes} /> : null}
        <Summary summary={o.summary} />
        <Blockers />
      </div>
      {/* Pinned under the tabs with its own scroll, so all of it is reachable whatever the
          left side is doing */}
      <aside className="space-y-8 xl:sticky xl:top-[9rem] xl:max-h-[calc(100dvh-9.75rem)] xl:self-start xl:overflow-y-auto xl:pr-1 xl:pb-4">
        <KeyNumbers numbers={o.key_numbers} />
        <ActionsPreview actions={o.actions} />
        {o.emails.length ? <EmailTrail emails={o.emails} /> : null}
        <Panel title="This run">
          {/* Label above value: the column is narrow and the outcome list is long */}
          <dl className="space-y-2.5 text-sm">
            <div>
              <dt className="text-graphite">Rules</dt>
              <dd className="tabular-nums">
                <RuleCounts />
              </dd>
            </div>
            <div>
              <dt className="text-graphite">Documents</dt>
              <dd>
                {o.submission.documents} from {plural(o.submission.files, "file")} and the email
              </dd>
            </div>
            <div>
              <dt className="text-graphite">Model cost</dt>
              <dd className="tabular-nums">${o.cost_usd.toFixed(2)}</dd>
            </div>
          </dl>
        </Panel>
      </aside>
    </div>
  );
}

const SMALL = ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten"];

/** A clause that opens with a small count reads better spelled out: '1 driver' -> 'One driver'. */
function opening(text: string): string {
  const m = /^(\d+)\b(.*)$/.exec(text);
  if (m && Number(m[1]) < SMALL.length) return SMALL[Number(m[1])] + m[2];
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/**
 * The second half of the verdict, written by code from the blockers and the request counts, never
 * by a model: what could decline it, then what the agent still owes.
 */
export function verdictSentence(declines: string[], needed: number): string | undefined {
  const parts: string[] = [];
  if (declines.length === 1) parts.push(declines[0]);
  else if (declines.length > 1) parts.push(`${declines.length} findings could decline it`);
  if (needed)
    parts.push(
      `${needed === 1 ? "1 item is" : `${needed} items are`} still needed from the agent before a quote`,
    );
  if (!parts.length) return undefined;
  return `${opening(parts.join(", and "))}.`;
}

function Summary({ summary }: { summary: Schemas["SummaryOut"] }) {
  const notes = new Map(summary.footnotes.map((f) => [f.n, f]));
  const analysis = useAnalysis(useSubmissionId());
  const model = analysis.data?.model;
  const refs = analysis.data?.refs ?? {};
  return (
    <Panel
      title="Summary"
      description={
        model && summary.sentences.length
          ? `Written by ${modelName(model)}. Code checks that every sentence cites a fact or a rule.`
          : "Written by the expert review once it runs. Code checks that every sentence cites a fact or a rule."
      }
    >
      <EarlierReview />
      <ApproveAnalysis />
      {summary.status !== "ok" &&
      summary.status !== "awaiting_approval" &&
      summary.status !== "running" &&
      summary.message ? (
        <p className="mb-3 rounded-[var(--radius-cell)] bg-unknown-soft px-3 py-2 text-sm text-ink">
          {summary.message}
        </p>
      ) : null}
      {summary.sentences.length ? (
        // Short paragraphs where the topic changes; the sentences themselves are as written
        <div
          className={clsx(
            "max-w-[68ch] space-y-3 font-serif text-[1.0625rem] leading-[1.65]",
            summary.stale && "text-graphite",
          )}
        >
          {paragraphs(summary.sentences, notes).map((para, p) => (
            <p key={p}>
              {para.map((s, i) => (
                <span key={i}>
                  {/* The last word and its sources stay on one line */}
                  {s.text.slice(0, s.text.lastIndexOf(" ") + 1)}
                  <span className="whitespace-nowrap">
                    {s.text.slice(s.text.lastIndexOf(" ") + 1)}
                    <Footnotes runs={clusters(s.footnotes)} notes={notes} refs={refs} />
                  </span>{" "}
                </span>
              ))}
            </p>
          ))}
        </div>
      ) : summary.status === "not_run" ? (
        <p className="text-graphite">The analyst has not run for this submission yet.</p>
      ) : null}
    </Panel>
  );
}

/** A sentence's sources as one superscript: a small gap after the text, groups apart with a
 * comma ("9, 29–32"), each group opening its sources in the lens. */
function Footnotes({
  runs,
  notes,
  refs,
}: {
  runs: number[][];
  notes: Parameters<typeof clusterTarget>[1];
  refs: Parameters<typeof clusterTarget>[2];
}) {
  const shown = runs
    .map((run) => ({ label: clusterLabel(run), target: clusterTarget(run, notes, refs) }))
    .filter((x) => x.target);
  if (!shown.length) return null;
  return (
    <sup className="ml-0.5 font-sans text-[0.68em] leading-none">
      {shown.map(({ label, target }, j) => (
        <Fragment key={label}>
          {j ? <span className="text-graphite">,</span> : null}
          {target ? <Footnote n={label} target={target} /> : null}
        </Fragment>
      ))}
    </sup>
  );
}

const STEP_NAME: Record<string, string> = {
  triage: "Reading pages",
  classify: "Recognising documents",
  extract: "Extracting facts",
  resolve: "Reconciling",
  rules: "Applying the guideline",
  analyze: "Expert review",
  actions: "Agent request",
};

/**
 * When a step's latest result was degraded or failed, say which and that the rest stands. Each
 * step's most recent result counts: a later rules-only rerun does not hide an earlier extraction
 * that could not finish.
 */
function RunHealth() {
  const id = useSubmissionId();
  const runs = useRuns(id);
  const latest = new Map<string, Schemas["StepOut"]>();
  for (const r of runs.data ?? []) {
    if (r.trigger === "replay" || r.status === "running") continue;
    for (const s of r.steps) if (!latest.has(s.name)) latest.set(s.name, s);
  }
  const hurt = [...latest.values()].filter((s) => s.status === "degraded" || s.status === "failed");
  if (!hurt.length) return null;
  const failed = hurt.some((s) => s.status === "failed");
  return (
    <section
      className={clsx(
        "rounded-[var(--radius-panel)] border px-4 py-3",
        failed ? "border-stop/40 bg-stop-soft" : "border-unknown/30 bg-unknown-soft",
      )}
    >
      <p className={clsx("font-semibold", failed ? "text-stop" : "text-ink")}>
        {failed ? "A step could not finish" : "Some steps finished with gaps"}:{" "}
        {hurt.map((s) => (STEP_NAME[s.name] ?? s.name).toLowerCase()).join(", ")}
      </p>
      <p className="mt-0.5 text-sm text-ink">
        {failed
          ? "Steps after it did not run; what is shown comes from earlier runs."
          : "Everything that does not depend on those steps is complete and shown."}{" "}
        {hurt
          .map((s) => s.error)
          .filter(Boolean)
          .slice(0, 2)
          .join(" ")}{" "}
        <Link href={`/submissions/${id}/audit`} className="font-semibold text-interstate">
          See the run
        </Link>
      </p>
    </section>
  );
}

function SecurityNotes({ notes }: { notes: Schemas["SecurityNoteOut"][] }) {
  return (
    <section className="rounded-[var(--radius-panel)] border border-warning/40 bg-warning-soft px-4 py-3">
      <p className="flex items-center gap-1.5 font-semibold text-warning">
        <ShieldAlert size={16} aria-hidden="true" /> Instructions found inside the documents
      </p>
      <p className="mt-0.5 text-sm">
        Treated as data and never followed: no model or rule acts on document text. Recorded in the
        audit trail.
      </p>
      <ul className="mt-2 space-y-1 text-sm">
        {notes.map((n, i) => (
          <li key={i}>
            <span className="font-semibold">
              {n.filename}, page {n.page}:
            </span>{" "}
            <q className="text-graphite">{n.text}</q>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** What stands in the way, in the Rules page's groups: what decides the route, each missing
 * answer and the rules it holds, and the checks that wait for the quote. Notes stay on Rules. */
function Blockers() {
  const id = useSubmissionId();
  // Reasons whose rule the last rerun changed get the same once-only wash as the Rules tab
  const { last } = useRerun();
  const changed = new Set((last?.changes ?? []).map((c) => c.rule_id));
  const rules = useRules(id);
  const all = rules.data?.rules ?? [];
  const needs = all
    .filter((r) => NEEDS.has(r.kind))
    .sort((a, b) => SEVERITY.indexOf(a.kind) - SEVERITY.indexOf(b.kind));
  const waiting = waitingGroups(all);
  const checks = all.filter((r) => r.kind === "check");
  const ruleLink = (r: Schemas["RuleOut"]) => (
    <Hint asChild content={<RuleHint rule={r} />}>
      <Link
        href={`/submissions/${id}/rules#${r.rule_id}`}
        className="font-medium hover:text-interstate"
      >
        <span className="font-mono text-xs text-graphite">{r.rule_id}</span> {r.name}
      </Link>
    </Hint>
  );
  if (!all.length) return null;
  return (
    <Panel title="What stands between this submission and a quote">
      {needs.length ? (
        <>
          <h3 className="mb-1 text-sm text-graphite">Decides the route</h3>
          <table className="mb-5 w-full text-base">
            <tbody>
              {needs.map((r) => {
                const k = kindOf(r);
                return (
                  <tr
                    key={`${r.rule_id}-${changed.has(r.rule_id) ? (last?.run_id ?? "") : ""}`}
                    className={clsx(
                      "border-b border-hairline align-baseline last:border-0",
                      changed.has(r.rule_id) && "rule-changed",
                    )}
                  >
                    <td className={clsx("w-36 py-2.5 pr-4 text-sm whitespace-nowrap", k.text)}>
                      <Hint content={statusHint(k.word)}>
                        <span
                          className={clsx("mr-2 inline-block size-[7px] rounded-full", k.dot)}
                          aria-hidden="true"
                        />
                        {k.word}
                      </Hint>
                    </td>
                    <td className="w-[34%] py-2.5 pr-4">{ruleLink(r)}</td>
                    <td className="py-2.5 text-sm text-graphite">{r.headline}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </>
      ) : null}
      {waiting.length ? (
        <>
          <h3 className="mb-1 text-sm text-graphite">Can&rsquo;t check yet</h3>
          <ul className="mb-5 border-t border-hairline">
            {waiting.map(([key, g]) => (
              <li key={key} className="border-b border-hairline py-2.5 text-sm">
                <span
                  className="mr-2 inline-block size-[7px] rounded-full bg-unknown"
                  aria-hidden="true"
                />
                <span className="text-ink">{g.label}</span>
                <span className="text-graphite">: </span>
                {g.blocks.map((r, i) => (
                  <span key={r.rule_id}>
                    {i ? ", " : ""}
                    <RuleRef ruleId={r.rule_id} />
                  </span>
                ))}
              </li>
            ))}
          </ul>
        </>
      ) : null}
      {checks.length ? (
        <>
          <h3 className="mb-1 text-sm text-graphite">Checked at quote</h3>
          <ul className="border-t border-hairline">
            {checks.map((r) => (
              <li key={r.rule_id} className="border-b border-hairline py-2.5 text-sm">
                {ruleLink(r)} <span className="text-graphite">{r.headline}</span>
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </Panel>
  );
}

/** The rules in the Rules page's groups and words. */
function RuleCounts() {
  const id = useSubmissionId();
  const rules = useRules(id);
  return <>{groupCounts(rules.data?.rules ?? [])}</>;
}

function KeyNumbers({ numbers }: { numbers: Schemas["KeyNumberOut"][] }) {
  return (
    <Panel title="Key numbers">
      {/* A compact table, not stat tiles: label, value, and the note on hover */}
      <table className="w-full text-sm">
        <tbody>
          {numbers.map((n) => {
            const target = n.field_id
              ? ({ kind: "field", fieldId: n.field_id, label: n.label } as const)
              : n.entity_ids.length
                ? ({ kind: "entity", entityIds: n.entity_ids, label: n.label } as const)
                : null;
            const value = (
              <span className={clsx("font-medium tabular-nums", n.conflict && "text-warning")}>
                {n.value}
              </span>
            );
            return (
              <tr
                key={n.key}
                className="border-b border-hairline last:border-0"
                title={n.note ?? undefined}
              >
                <th scope="row" className="py-1.5 pr-3 text-left font-normal text-graphite">
                  {n.label}
                </th>
                <td className="py-1.5 text-right whitespace-nowrap">
                  {target ? <EvidenceLink target={target}>{value}</EvidenceLink> : value}
                  {n.conflict ? <span className="sr-only"> (the documents disagree)</span> : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </Panel>
  );
}

function ActionsPreview({ actions }: { actions: Schemas["ActionsPreview"] }) {
  const id = useSubmissionId();
  return (
    <Panel
      title="Next actions"
      actions={
        <Link
          href={`/submissions/${id}/actions`}
          className="text-sm font-semibold text-interstate hover:underline"
        >
          Open
        </Link>
      }
    >
      <ul className="space-y-1.5 text-sm">
        {Object.entries(actions.group_labels).map(([group, label]) => (
          <li key={group} className="flex justify-between">
            <span className="text-graphite">{label}</span>
            <span className="font-medium tabular-nums">{actions.items_by_group[group] ?? 0}</span>
          </li>
        ))}
      </ul>
      {actions.draft_current === false ? (
        <p className="mt-2 text-xs text-warning">
          The draft email is out of date with the request.
        </p>
      ) : actions.draft_current ? (
        <p className="mt-2 text-xs text-graphite">
          Draft email ready. Nothing is sent automatically.
        </p>
      ) : null}
    </Panel>
  );
}
