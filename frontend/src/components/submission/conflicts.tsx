"use client";

import clsx from "clsx";
import { Check, ChevronDown, Clock, GitCompareArrows, Lightbulb, RotateCcw, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { useLens } from "@/components/evidence/lens";
import { SourceCrop } from "@/components/evidence/source-crop";
import { useRerun } from "@/components/submission/rerun";
import { RuleRef } from "@/components/submission/rule-ref";
import { useSubmissionId } from "@/components/submission/shell";
import { type DocInfo, locationText, plainCells, sourceName } from "@/lib/source-names";
import { ConfidenceBand } from "@/components/ui/chips";
import { Hint } from "@/components/ui/hint";
import { Button, Empty, ErrorNote, Kbd, Skeleton } from "@/components/ui/primitives";
import type { Schemas } from "@/lib/api";
import { formatDateTime, personName, titleCase } from "@/lib/format";
import {
  useConflicts,
  useDocuments,
  useFacts,
  useOverview,
  usePages,
  useReopenConflict,
  useResolveConflict,
} from "@/lib/queries";
import { useSession } from "@/lib/session";

/**
 * The conflict queue: conflicts that change a rule outcome first. Settling one reruns resolution
 * and the rules at once and shows what changed. Keyboard: J and K move, E opens the evidence,
 * 1 to 9 open a value's source.
 */
const SEVERITY_RANK: Record<string, number> = { high: 0, medium: 1, low: 2 };
const SEVERITY_DOT: Record<string, string> = {
  high: "bg-stop",
  medium: "bg-warning",
  low: "bg-note",
};
const SEVERITY_TEXT: Record<string, string> = {
  high: "text-stop",
  medium: "text-warning",
  low: "text-note",
};

export function Conflicts() {
  const id = useSubmissionId();
  const conflicts = useConflicts(id);
  const lens = useLens();
  const [active, setActive] = useState(0);
  // Which conflicts are open in place; until someone toggles one, the first blocking one is
  const [expanded, setExpanded] = useState<Set<string> | null>(null);
  // The value being chosen: a number key or "Use this value" picks it and asks for a reason
  const [choice, setChoice] = useState<{ id: string; index: number } | null>(null);
  const { publish } = useRerun();
  const reopen = useReopenConflict(id);
  const refs = useRef<(HTMLElement | null)[]>([]);

  const all = conflicts.data ?? [];
  // The API lists them blocking first, then high, medium, low severity
  const open = all
    .filter((c) => !c.resolution && c.type !== "reference")
    .sort(
      (a, b) =>
        Number(b.blocking_rules.length > 0) - Number(a.blocking_rules.length > 0) ||
        SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity] ||
        a.title.localeCompare(b.title),
    );
  // Settled by a person or a document, or by code from a reference (a VIN's own decode)
  const resolved = all.filter((c) => c.resolution || c.type === "reference");
  // Settled by a person, and by code from a VIN's own decode: told apart in the count
  const byPerson = resolved.filter((c) => c.resolution);
  const byCode = resolved.filter((c) => !c.resolution);
  const firstBlocking = open.find((c) => c.blocking_rules.length) ?? open[0];
  const isOpen = (c: Schemas["ConflictOut"]) =>
    expanded ? expanded.has(c.id) : c.id === firstBlocking?.id;
  const toggle = (c: Schemas["ConflictOut"]) =>
    setExpanded((prev) => {
      const next = new Set(prev ?? (firstBlocking ? [firstBlocking.id] : []));
      if (next.has(c.id)) next.delete(c.id);
      else next.add(c.id);
      return next;
    });

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.target instanceof HTMLElement && e.target.closest("input, textarea, select")) return;
      if (e.metaKey || e.ctrlKey || e.altKey || !open.length) return;
      const c = open[active];
      if (e.key === "j" || e.key === "J") {
        e.preventDefault();
        setActive((i) => Math.min(i + 1, open.length - 1));
      } else if (e.key === "k" || e.key === "K") {
        e.preventDefault();
        setActive((i) => Math.max(i - 1, 0));
      } else if (
        e.key === "Enter" &&
        c &&
        !(e.target instanceof HTMLElement && e.target.closest("button, a, summary"))
      ) {
        e.preventDefault();
        toggle(c);
      } else if (
        (e.key === "e" || e.key === "E") &&
        c &&
        !(document.activeElement as HTMLElement | null)?.dataset?.evidence
      ) {
        lens.open({ kind: "field", fieldId: c.field_id, label: c.title });
      } else if (/^[1-9]$/.test(e.key) && c && c.candidates[Number(e.key) - 1]) {
        // The key on "Use this value": picks that value and opens the card to ask why
        e.preventDefault();
        if (!isOpen(c)) toggle(c);
        setChoice({ id: c.id, index: Number(e.key) - 1 });
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- toggle reads the latest state
  }, [active, open, lens]);

  useEffect(() => {
    refs.current[active]?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [active]);

  if (conflicts.isPending) return <Skeleton lines={10} />;
  if (conflicts.error)
    return <ErrorNote title="Conflicts could not be loaded">{conflicts.error.message}</ErrorNote>;
  if (!all.length) {
    return (
      <Empty icon={GitCompareArrows} title="The documents agree">
        Every fact that appears in more than one document says the same thing.
      </Empty>
    );
  }

  const blocking = open.filter((c) => c.blocking_rules.length).length;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-graphite">
          {open.length} facts the documents disagree on
          {byPerson.length ? `, ${byPerson.length} resolved` : ""}
          {byCode.length ? `, ${byCode.length} settled by VIN decode` : ""}.{" "}
          {blocking
            ? `${blocking} ${blocking === 1 ? "changes a rule outcome and comes" : "change a rule outcome and come"} first. `
            : ""}
          Nothing is picked silently: a person chooses, with a reason, and the rules run again.
        </p>
        <p className="flex items-center gap-1.5 text-xs text-graphite">
          <Kbd>J</Kbd>
          <Kbd>K</Kbd> move <Kbd>Enter</Kbd> open <Kbd>E</Kbd> evidence <Kbd>1</Kbd>-<Kbd>9</Kbd>{" "}
          use a value
        </p>
      </div>
      <ol className="border-t border-hairline">
        {open.map((c, i) => (
          <ConflictCard
            key={c.id}
            c={c}
            active={i === active}
            expanded={isOpen(c)}
            choice={choice?.id === c.id ? choice.index : null}
            onChoose={(index) => setChoice(index === null ? null : { id: c.id, index })}
            onToggle={() => {
              setActive(i);
              toggle(c);
            }}
            onFocus={() => setActive(i)}
            onRerun={(r) =>
              publish({
                ...r,
                what: `${c.title} resolved`,
                undo: () => reopen.mutateAsync(c.id),
                undone: `${c.title} reopened`,
              })
            }
            ref={(el) => {
              refs.current[i] = el;
            }}
          />
        ))}
      </ol>
      {resolved.length ? (
        <section>
          <h2 className="mb-2 font-display text-md font-semibold text-graphite">
            Resolved ({resolved.length})
          </h2>
          <ul className="space-y-2">
            {resolved.map((c) => (
              <ResolvedRow
                key={c.id}
                c={c}
                onRerun={(r) => publish({ ...r, what: `${c.title} reopened` })}
              />
            ))}
          </ul>
        </section>
      ) : null}
    </div>
  );
}

function ConflictCard({
  c,
  active,
  expanded,
  choice,
  onChoose,
  onToggle,
  onFocus,
  onRerun,
  ref,
}: {
  c: Schemas["ConflictOut"];
  active: boolean;
  expanded: boolean;
  choice: number | null;
  onChoose: (index: number | null) => void;
  onToggle: () => void;
  onFocus: () => void;
  onRerun: (r: Schemas["RerunOut"]) => void;
  ref: (el: HTMLElement | null) => void;
}) {
  const id = useSubmissionId();
  const lens = useLens();
  const session = useSession();
  const resolve = useResolveConflict(id);
  const documents = useDocuments(id);
  const overview = useOverview(id);
  const [reason, setReason] = useState("");
  const [peek, setPeek] = useState<string | null>(null);
  // One fact per source, to show the exact place it was read
  const firstFacts = expanded
    ? c.candidates.flatMap((cand) => cand.sources.map((s) => s.fact_ids[0])).filter(Boolean)
    : [];
  const facts = useFacts(firstFacts);
  const pages = usePages(id);
  const factById = new Map((facts.data ?? []).map((f) => [f.id, f]));
  const pageById = new Map((pages.data ?? []).map((p) => [p.id, p]));
  const docs: DocInfo[] = documents.data ?? [];
  const docById = new Map(docs.map((d) => [d.id, d]));
  const applicant = overview.data?.applicant.legal_name ?? "";
  const cols = Math.min(c.candidates.length, 3);

  const openSource = (
    cand: Schemas["ConflictOut"]["candidates"][number],
    s: (typeof cand.sources)[number],
  ) => {
    onFocus();
    if (s.fact_ids.length)
      lens.open({
        kind: "facts",
        factIds: s.fact_ids,
        label: `${c.title}: ${String(cand.display)} (${s.title})`,
        note: workedOut([s]),
      });
    else lens.open({ kind: "field", fieldId: c.field_id, label: c.title });
  };

  return (
    <li
      ref={ref}
      id={`conflict-${c.id}`}
      data-hash-select=""
      aria-current={active ? "true" : undefined}
      className={clsx(
        "scroll-mt-40 border-b border-hairline",
        active && "shadow-[inset_2px_0_0_var(--interstate)]",
      )}
    >
      {/* Field and its values on the left; how much it matters and what it changes on the right */}
      <div className="flex items-baseline gap-4 px-3 py-3 hover:bg-surface/60">
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={expanded}
          // A fixed first column, so every row's values start at the same place and the list
          // scans as a table; source counts live in the open card
          className="grid min-w-0 flex-1 grid-cols-[19rem_minmax(0,1fr)] items-baseline gap-4 text-left"
        >
          <span className="font-semibold">{c.title}</span>
          <span className="truncate text-sm text-graphite">
            {c.candidates.map((cand) => String(cand.display)).join(" vs ")}
          </span>
        </button>
        <span className="inline-flex shrink-0 items-baseline gap-1.5 text-sm whitespace-nowrap">
          <span
            className={clsx(
              "inline-block size-[7px] translate-y-[-1px] rounded-full",
              SEVERITY_DOT[c.severity] ?? "bg-graphite",
            )}
            aria-hidden="true"
          />
          <span className={SEVERITY_TEXT[c.severity] ?? "text-graphite"}>
            {titleCase(c.severity)}
          </span>
          {c.blocking_rules.length ? (
            <span className="text-stop">
              {" · "}Changes{" "}
              {c.blocking_rules.map((r, i) => (
                <span key={r}>
                  {i ? ", " : ""}
                  <RuleRef ruleId={r} className="text-stop" />
                </span>
              ))}
            </span>
          ) : null}
        </span>
        <button
          type="button"
          onClick={onToggle}
          aria-label={expanded ? "Close" : "Open"}
          className="shrink-0 text-graphite hover:text-ink"
        >
          <ChevronDown
            size={16}
            aria-hidden="true"
            className={clsx("transition-transform", expanded && "rotate-180")}
          />
        </button>
      </div>

      {expanded ? (
        <div onClick={onFocus}>
          {c.difference ? <p className="px-3 pb-2 text-sm text-graphite">{c.difference}</p> : null}
          {/* One equal column per value, across the full card */}
          <div
            className="grid border-t border-hairline"
            style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}
          >
            {c.candidates.map((cand, i) => {
              const srcs = cand.sources;
              const chosen = choice === i;
              const all = srcs.flatMap((s) => s.fact_ids);
              return (
                <div
                  key={i}
                  className={clsx(
                    "flex flex-col gap-2 px-3 py-3",
                    i % cols && "border-l border-hairline",
                    chosen && "bg-interstate-soft/40",
                  )}
                >
                  <button
                    type="button"
                    data-evidence=""
                    onClick={(e) => {
                      e.stopPropagation();
                      onFocus();
                      if (all.length)
                        lens.open({
                          kind: "facts",
                          factIds: all,
                          label: `${c.title}: ${String(cand.display)}`,
                          note: workedOut(srcs),
                        });
                      else lens.open({ kind: "field", fieldId: c.field_id, label: c.title });
                    }}
                    className="self-start text-left text-sm font-semibold hover:text-interstate"
                  >
                    {String(cand.display)}
                  </button>
                  {/* Every source on one line; the crop of the page shows on hover or focus */}
                  <ul className="space-y-0.5 text-sm" aria-label="Sources">
                    {srcs.map((s, j) => {
                      const key = `${i}-${j}`;
                      const fact = factById.get(s.fact_ids[0] ?? "");
                      const where = locationText(s.locations);
                      const doc = sourceName(
                        s.title,
                        docById.get(s.document_id),
                        docs,
                        applicant,
                        s.locations,
                      );
                      return (
                        <li
                          key={key}
                          onMouseEnter={() => setPeek(key)}
                          onMouseLeave={() => setPeek((p) => (p === key ? null : p))}
                        >
                          <button
                            type="button"
                            data-evidence=""
                            onFocus={() => setPeek(key)}
                            onBlur={() => setPeek((p) => (p === key ? null : p))}
                            onClick={(e) => {
                              e.stopPropagation();
                              openSource(cand, s);
                            }}
                            className="text-left text-interstate underline-offset-2 hover:underline"
                          >
                            {doc}
                            {where ? `, ${where}` : ""}
                          </button>
                          {/* How well this source was read; it never ranks one value over another */}
                          {s.confidence ? (
                            <Hint
                              content={
                                <>
                                  <span className="font-semibold">
                                    {titleCase(s.confidence)} confidence
                                  </span>
                                  {s.confidence_reasons.map((r) => (
                                    <span key={r} className="block text-graphite">
                                      {plainCells(r, doc)}
                                    </span>
                                  ))}
                                </>
                              }
                              className="ml-2 inline-flex no-underline"
                            >
                              <ConfidenceBand band={s.confidence} hinted />
                            </Hint>
                          ) : null}
                          {peek === key && fact && !fact.cell_ref ? (
                            <SourceCrop
                              fact={fact}
                              page={pageById.get(fact.page_id ?? "")}
                              onOpen={() => openSource(cand, s)}
                            />
                          ) : null}
                        </li>
                      );
                    })}
                  </ul>
                  {/* Explanations sit under the sources, never in place of one */}
                  {[...new Set(srcs.flatMap((s) => (s.note ? [s.note] : [])))].map((n) => (
                    <p key={n} className="text-sm text-graphite">
                      {n}
                    </p>
                  ))}
                  {session.can("review") ? (
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        onFocus();
                        onChoose(chosen ? null : i);
                      }}
                      aria-pressed={chosen}
                      aria-keyshortcuts={i < 9 ? String(i + 1) : undefined}
                      className={clsx(
                        // Pushed to the bottom so every column's button lines up
                        "mt-auto inline-flex items-center gap-2 self-start rounded-[var(--radius-control)] border px-2.5 py-1 text-sm",
                        chosen
                          ? "border-interstate bg-interstate text-on-interstate"
                          : "border-hairline text-ink hover:bg-surface",
                      )}
                    >
                      {chosen ? "Chosen" : "Use this value"}
                      {i < 9 ? <Kbd>{i + 1}</Kbd> : null}
                    </button>
                  ) : null}
                </div>
              );
            })}
          </div>
          {choice !== null ? (
            <form
              className="flex flex-wrap items-start gap-2 border-t border-hairline bg-interstate-soft/40 px-3 py-3"
              onClick={(e) => e.stopPropagation()}
              onSubmit={(e) => {
                e.preventDefault();
                if (!reason.trim()) return;
                // The card unmounts once its conflict is settled, so report through the parent.
                void resolve
                  .mutateAsync({ conflictId: c.id, candidate: choice, reason })
                  .then((r) => {
                    onChoose(null);
                    setReason("");
                    onRerun(r);
                  })
                  .catch(() => undefined);
              }}
            >
              <label className="min-w-72 flex-1">
                <span className="mb-1 block text-sm font-semibold">
                  Why is {String(c.candidates[choice]?.display)} right?
                </span>
                <input
                  autoFocus
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  placeholder="For example: confirmed with the agent by phone"
                  className="h-8 w-full rounded-[var(--radius-cell)] border border-hairline bg-surface px-2 text-sm"
                />
              </label>
              <div className="flex items-center gap-2 self-end">
                <Button
                  type="submit"
                  variant="primary"
                  icon={Check}
                  busy={resolve.isPending}
                  disabled={!reason.trim()}
                >
                  Resolve conflict
                </Button>
                <Button variant="quiet" icon={X} onClick={() => onChoose(null)}>
                  Cancel
                </Button>
              </div>
              {resolve.error ? (
                <p className="w-full text-sm text-stop">{resolve.error.message}</p>
              ) : null}
            </form>
          ) : null}
          {c.hint || c.notes.length ? (
            <div className="space-y-1 border-t border-hairline px-3 py-2.5 text-sm">
              {c.hint ? (
                <p className="flex items-start gap-1.5">
                  <Lightbulb
                    size={14}
                    className="mt-0.5 shrink-0 text-interstate"
                    aria-hidden="true"
                  />
                  {c.hint}
                </p>
              ) : null}
              {c.notes.map((n) => {
                const [label, ...rest] = n.split(": ");
                return (
                  <p key={n}>
                    {rest.length ? (
                      <>
                        <span className="font-semibold">{label}</span>
                        <span className="text-graphite">: {rest.join(": ")}</span>
                      </>
                    ) : (
                      n
                    )}
                  </p>
                );
              })}
            </div>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

function ResolvedRow({
  c,
  onRerun,
}: {
  c: Schemas["ConflictOut"];
  onRerun: (r: Schemas["RerunOut"]) => void;
}) {
  const id = useSubmissionId();
  const reopen = useReopenConflict(id);
  const confirm = useResolveConflict(id);
  const session = useSession();
  const r = c.resolution;
  if (!r && c.type === "reference") {
    return (
      <li className="flex flex-wrap items-center gap-3 rounded-[var(--radius-panel)] border border-guide/30 px-4 py-2.5">
        <Check size={16} className="text-guide" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <p className="font-semibold">{c.title}</p>
          <p className="text-sm text-graphite">Settled by code from the VIN decode; not counted</p>
        </div>
      </li>
    );
  }
  if (!r) return null;
  if (r.pending) {
    // Settled by a document the agent sent: a person confirms it, which records a resolution
    const index = c.candidates.findIndex((x) => x.display === r.display);
    return (
      <li className="flex flex-wrap items-center gap-3 rounded-[var(--radius-panel)] border border-warning/40 bg-warning-soft px-4 py-2.5">
        <Clock size={16} className="text-warning" aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <p>
            <span className="font-semibold">{c.title}</span>
            <span className="text-graphite"> settled as </span>
            <span className="font-semibold">{r.display}</span>
          </p>
          <p className="text-sm">{r.reason}</p>
        </div>
        {session.can("review") && index >= 0 ? (
          <Button
            size="sm"
            busy={confirm.isPending}
            onClick={() =>
              void confirm
                .mutateAsync({
                  conflictId: c.id,
                  candidate: index,
                  reason: `Confirmed: ${r.reason
                    .split(":")[0]
                    .replace(/, pending confirmation$/, "")
                    .toLowerCase()}`,
                })
                .then(onRerun)
                .catch(() => undefined)
            }
          >
            Confirm
          </Button>
        ) : null}
      </li>
    );
  }
  return (
    <li className="flex flex-wrap items-center gap-3 rounded-[var(--radius-panel)] border border-guide/30 px-4 py-2.5">
      <Check size={16} className="text-guide" aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <p>
          <span className="font-semibold">{c.title}</span>
          <span className="text-graphite"> settled as </span>
          <span className="font-semibold">{r.display}</span>
        </p>
        <p className="text-sm text-graphite">
          Resolved by {personName(r.actor)}, {r.reason}{" "}
          <span className="text-graphite">({formatDateTime(r.at)})</span>
        </p>
      </div>
      {session.can("review") ? (
        <Button
          size="sm"
          variant="quiet"
          icon={RotateCcw}
          busy={reopen.isPending}
          onClick={() =>
            void reopen
              .mutateAsync(c.id)
              .then(onRerun)
              .catch(() => undefined)
          }
        >
          Reopen
        </Button>
      ) : null}
    </li>
  );
}

/** A value code worked out (not read off a page) says so, and how, above the facts it used. */
function workedOut(
  sources: { doc_type?: string | null; note?: string | null }[],
): string | undefined {
  const derived = sources.find((s) => s.doc_type === "derived");
  return derived ? `Worked out by code from these facts. ${derived.note ?? ""}`.trim() : undefined;
}
