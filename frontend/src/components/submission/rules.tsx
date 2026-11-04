"use client";

import clsx from "clsx";
import { motion, useReducedMotion } from "motion/react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";

import { EvidenceLink } from "@/components/evidence/evidence-link";
import { useRerun } from "@/components/submission/rerun";
import { RuleRef } from "@/components/submission/rule-ref";
import { useSubmissionId } from "@/components/submission/shell";
import { OUTCOMES } from "@/components/ui/chips";
import { Hint } from "@/components/ui/hint";
import { ErrorNote, Skeleton } from "@/components/ui/primitives";
import type { Schemas } from "@/lib/api";
import { statusHint } from "@/lib/glossary";
import { kindOf, NEEDS, SEVERITY, waitingGroups } from "@/lib/rule-groups";
import { useSourceName } from "@/lib/source-names";
import { useEntities, useFacts, useOverview, useRules, useRuleset } from "@/lib/queries";

type Rule = Schemas["RuleOut"];
type FactLabel = { entity: string; field: string };

const factText = (l: FactLabel | undefined) => (l ? `${l.entity}: ${l.field}` : undefined);

/** The facts a rule read, grouped by what they belong to, in the order first read. */
function groupFacts(ids: string[], labels: Map<string, FactLabel>): [string, string[]][] {
  const groups = new Map<string, string[]>();
  for (const fid of ids) {
    const entity = labels.get(fid)?.entity ?? "Other";
    groups.set(entity, [...(groups.get(entity) ?? []), fid]);
  }
  return [...groups.entries()];
}

const HINT_WORD: Record<string, string> = { missing_document: "missing", check: "at quote" };

type Tab = "all" | "needs" | "waiting" | "notes" | "passed" | "na";

/** The view lives in the link (?view=section), so it reads the search params under Suspense. */
export function Rules() {
  return (
    <Suspense fallback={<Skeleton lines={12} />}>
      <RulesView />
    </Suspense>
  );
}

function RulesView() {
  const id = useSubmissionId();
  const rules = useRules(id);
  // A cleared decline waits for an underwriter: the route stays a potential decline until then
  const pending = (useOverview(id).data?.submission.pending_confirmations ?? 0) > 0;
  const entities = useEntities(id);
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const bySection = params.get("view") === "section";
  const [tab, setTab] = useState<Tab>("all");
  // A link to #RULE-ID opens that rule; otherwise the first rule on screen
  const [picked, setSelected] = useState<string | null>(() =>
    typeof window === "undefined" ? null : window.location.hash.slice(1) || null,
  );
  const { last } = useRerun();
  const changed = useMemo(() => new Map((last?.changes ?? []).map((c) => [c.rule_id, c])), [last]);

  const labels = useMemo(() => {
    const m = new Map<string, FactLabel>();
    for (const e of entities.data ?? [])
      for (const f of e.fields) m.set(f.id, { entity: e.label, field: f.label });
    return m;
  }, [entities.data]);

  const all = useMemo(() => rules.data?.rules ?? [], [rules.data]);
  // Most serious first, then guideline order within a kind
  const needs = all
    .filter((r) => NEEDS.has(r.kind))
    .sort((a, b) => SEVERITY.indexOf(a.kind) - SEVERITY.indexOf(b.kind));
  const waiting = waitingGroups(all);
  const checks = all.filter((r) => r.kind === "check");
  const notes = all.filter((r) => r.kind === "flag");
  const passed = all.filter((r) => r.kind === "pass");
  const na = all.filter((r) => r.kind === "n/a");
  const settled = [...passed, ...na];
  const unknownCount = all.filter((r) => r.kind === "unknown").length;
  // The final route sums up the others: a line of its own, so the tabs add up to All
  const summary = all.find((r) => r.kind === "summary");
  const counted = all.length - (summary ? 1 : 0);

  // The stops j and k move through: the rule rows on screen, top to bottom. A row's stop is its
  // rule id; a row under a missing answer gets its own (waitStop), since a rule can wait on more
  // than one answer and sit in another group too. The answers themselves are headings, not stops.
  const order = useMemo(() => {
    if (bySection) return all.map((r) => r.rule_id);
    const seq: string[] = [];
    const ids = (rs: Rule[]) => rs.map((r) => r.rule_id);
    if (tab === "all" || tab === "needs") seq.push(...ids(needs));
    if (tab === "all" || tab === "waiting") {
      for (const [key, g] of waiting)
        seq.push(...[...g.blocks, ...g.adds].map((r) => waitStop(key, r.rule_id)));
      seq.push(...ids(checks));
    }
    if (tab === "all" || tab === "notes") seq.push(...ids(notes));
    if (tab === "passed") seq.push(...ids(passed));
    if (tab === "na") seq.push(...ids(na));
    return [...new Set(seq)];
  }, [all, bySection, tab, needs, waiting, checks, notes, passed, na]);

  // A rule id from a link or another page selects its first row on screen
  const selected =
    picked && order.includes(picked)
      ? picked
      : picked && all.some((r) => r.rule_id === picked)
        ? (order.find((s) => s === picked || s.endsWith(WAIT_RULE + picked)) ?? picked)
        : (order[0] ?? null);
  const ruleOf = (stop: string | null) =>
    stop?.startsWith(WAIT) ? (stop.split(WAIT_RULE)[1] ?? null) : stop;

  function select(stop: string) {
    setSelected(stop);
    history.replaceState(null, "", `#${ruleOf(stop) ?? ""}`);
  }

  // On a client navigation (a footnote's "Open on the Rules tab") the router writes the hash
  // after this mounts: read it again then, and bring the rule's row into view
  useEffect(() => {
    const go = () => {
      const id = decodeURIComponent(window.location.hash.slice(1));
      if (!id) return;
      setSelected(id);
      window.setTimeout(
        () => document.getElementById(stopId(id))?.scrollIntoView({ block: "center" }),
        50,
      );
    };
    const t = window.setTimeout(go, 0);
    window.addEventListener("hashchange", go);
    return () => {
      window.clearTimeout(t);
      window.removeEventListener("hashchange", go);
    };
  }, []);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.target instanceof HTMLElement && e.target.closest("input, textarea, select")) return;
      if (e.key !== "j" && e.key !== "k") return;
      const at = selected ? order.indexOf(selected) : -1;
      const next = order[Math.min(Math.max(at + (e.key === "j" ? 1 : -1), 0), order.length - 1)];
      if (next) {
        setSelected(next);
        history.replaceState(
          null,
          "",
          `#${next.startsWith(WAIT) ? next.split(WAIT_RULE)[1] : next}`,
        );
        // Focus follows, so Enter opens the row and the focus ring shows where you are
        const el = document.getElementById(stopId(next));
        el?.scrollIntoView({ block: "nearest" });
        el?.querySelector<HTMLElement>("[data-row]")?.focus({ preventScroll: true });
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [order, selected]);

  if (rules.isPending) return <Skeleton lines={12} />;
  if (rules.error)
    return <ErrorNote title="Rules could not be loaded">{rules.error.message}</ErrorNote>;

  const currentId = ruleOf(selected);
  const current = all.find((r) => r.rule_id === currentId) ?? null;
  const rowAt = (r: Rule, stop: string, extra?: { summary?: string | null; also?: boolean }) => (
    <RuleRow
      key={`${stop}-${changed.has(r.rule_id) ? (last?.run_id ?? "") : ""}`}
      r={r}
      stop={stop}
      summary={extra && "summary" in extra ? extra.summary : r.headline}
      also={extra?.also}
      active={stop === selected}
      changed={changed.has(r.rule_id)}
      onSelect={() => select(stop)}
    />
  );
  const row = (r: Rule) => rowAt(r, r.rule_id);
  const setView = (section: boolean) => {
    const next = new URLSearchParams(params.toString());
    if (section) next.set("view", "section");
    else next.delete("view");
    const qs = next.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  };

  return (
    <div className="space-y-4">
      {summary && rules.data.decision ? (
        // The final route is this line, not a row: a link to #rule-ID for it lands here
        <p id={`rule-${summary.rule_id}`} className="scroll-mt-48 text-sm text-graphite">
          Route: <span className="font-medium text-ink">{rules.data.decision.headline}</span>
          {rules.data.decision.potential_decline ? (
            <span className="text-stop">, potential decline</span>
          ) : pending ? (
            <span className="text-stop">
              , potential decline until the pending change is confirmed
            </span>
          ) : null}
          . The final route (<RuleRef ruleId={summary.rule_id} />) sums up the {counted} rules
          below.
        </p>
      ) : null}
      <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-2">
        {bySection ? (
          <p className="text-sm text-graphite">
            {all.length} rules in guideline order, each quoting the line it enforces.
          </p>
        ) : (
          <div className="flex flex-wrap gap-5" role="group" aria-label="Show">
            <TabButton active={tab === "all"} onClick={() => setTab("all")}>
              All {counted}
            </TabButton>
            <TabButton active={tab === "needs"} onClick={() => setTab("needs")}>
              Needs you {needs.length}
            </TabButton>
            <TabButton active={tab === "waiting"} onClick={() => setTab("waiting")}>
              Can&rsquo;t check yet {unknownCount + checks.length}
            </TabButton>
            <TabButton active={tab === "notes"} onClick={() => setTab("notes")}>
              Notes {notes.length}
            </TabButton>
            <TabButton active={tab === "passed"} onClick={() => setTab("passed")}>
              Passed {passed.length}
            </TabButton>
            <TabButton active={tab === "na"} onClick={() => setTab("na")}>
              Not applicable {na.length}
            </TabButton>
          </div>
        )}
        <p className="text-sm text-graphite">
          View:{" "}
          <button
            type="button"
            aria-pressed={!bySection}
            onClick={() => setView(false)}
            className={clsx(!bySection ? "text-ink" : "text-interstate hover:underline")}
          >
            by what needs you
          </button>{" "}
          or{" "}
          <button
            type="button"
            aria-pressed={bySection}
            onClick={() => setView(true)}
            className={clsx(bySection ? "text-ink" : "text-interstate hover:underline")}
          >
            by guideline section
          </button>
        </p>
      </div>

      {changed.size ? (
        <p className="border-l-2 border-interstate py-1 pl-3 text-sm">
          Changed by the last rerun:{" "}
          {[...changed.values()].map((c, i) => (
            <span key={c.rule_id}>
              {i ? ", " : ""}
              <RuleRef ruleId={c.rule_id} />{" "}
              <span className="text-graphite">
                {c.potential_before
                  ? "potential decline"
                  : (OUTCOMES[c.before]?.label ?? c.before).toLowerCase()}{" "}
                to{" "}
              </span>
              {kindOf(all.find((r) => r.rule_id === c.rule_id) ?? ({} as Rule)).word.toLowerCase()}
            </span>
          ))}
        </p>
      ) : null}

      <div className="grid grid-cols-1 gap-x-10 gap-y-6 xl:grid-cols-[minmax(0,1fr)_26rem]">
        <div className="min-w-0">
          {bySection ? (
            [...new Set(all.map((r) => r.section))].map((section) => (
              <Group key={section} title={section}>
                {all.filter((r) => r.section === section).map(row)}
              </Group>
            ))
          ) : (
            <>
              {(tab === "all" || tab === "needs") && needs.length ? (
                <Group title="Decides the route">{needs.map(row)}</Group>
              ) : null}
              {(tab === "all" || tab === "waiting") && waiting.length ? (
                <Group
                  title="Can't check yet"
                  note="The documents can't answer these yet. Each line is one missing answer; once the agent gives it, the rules under it can be decided."
                >
                  {waiting.map(([key, g]) => (
                    <WaitingBlock key={key} waitKey={key} group={g} row={rowAt} />
                  ))}
                </Group>
              ) : null}
              {(tab === "all" || tab === "waiting") && checks.length ? (
                <Group title="Checked at quote">{checks.map(row)}</Group>
              ) : null}
              {(tab === "all" || tab === "notes") && notes.length ? (
                <Group title="Notes">{notes.map(row)}</Group>
              ) : null}
              {tab === "all" && settled.length ? (
                <Settled rules={settled} row={row} selected={ruleOf(selected)} />
              ) : null}
              {tab === "passed" && passed.length ? (
                <Group title="Passed">{passed.map(row)}</Group>
              ) : null}
              {tab === "na" && na.length ? (
                <Group title="Not applicable">{na.map(row)}</Group>
              ) : null}
            </>
          )}
        </div>
        <aside
          aria-label="Rule detail"
          className="xl:sticky xl:top-[6.75rem] xl:max-h-[calc(100dvh-7.5rem)] xl:self-start xl:overflow-y-auto"
        >
          {current ? (
            <RuleDetail
              key={current.rule_id}
              r={current}
              labels={labels}
              all={all}
              change={changed.get(current.rule_id)}
            />
          ) : (
            <p className="text-sm text-graphite">Select a rule to see how Lane checked it.</p>
          )}
        </aside>
      </div>
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={clsx(
        "border-b-2 py-1 text-sm tabular-nums",
        active ? "border-interstate text-ink" : "border-transparent text-graphite hover:text-ink",
      )}
    >
      {children}
    </button>
  );
}

function Group({
  title,
  note,
  children,
}: {
  title: string;
  note?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="mb-6">
      <h2 className="text-sm text-graphite">{title}</h2>
      {note ? <p className="mt-0.5 max-w-[70ch] text-[0.8125rem] text-graphite">{note}</p> : null}
      <div className="mb-1.5" />
      <ul className="border-t border-hairline">{children}</ul>
    </section>
  );
}

/** A status mark and word, with what it means for the submission on hover or focus. */
function Status({ r }: { r: Rule }) {
  const k = kindOf(r);
  const result = sameResult(r);
  if (result)
    return (
      <Hint content={`Every value the documents give leads to ${result}.`}>
        <span className={clsx("whitespace-nowrap", k.text)}>
          <span
            className={clsx("mr-2 inline-block size-[7px] rounded-full", k.dot)}
            aria-hidden="true"
          />
          {result.replace(/ underwriting$/i, "")}
        </span>
      </Hint>
    );
  return (
    <Hint content={statusHint(HINT_WORD[r.kind] ?? r.kind.replace("_", " "))}>
      <span className={clsx("whitespace-nowrap", k.text)}>
        <span
          className={clsx("mr-2 inline-block size-[7px] rounded-full", k.dot)}
          aria-hidden="true"
        />
        {k.word}
      </span>
    </Hint>
  );
}

function RuleRow({
  r,
  stop,
  summary,
  also,
  active,
  changed,
  onSelect,
}: {
  r: Rule;
  stop: string;
  summary?: string | null;
  also?: boolean; // a rule that already has an outcome and only also needs the answer above
  active: boolean;
  changed: boolean;
  onSelect: () => void;
}) {
  return (
    <li
      id={stopId(stop)}
      onClick={onSelect}
      className={clsx(
        "grid cursor-pointer scroll-mt-48 grid-cols-[8.5rem_minmax(0,1fr)] items-baseline gap-3 border-b border-hairline px-1 text-sm transition-colors",
        ROW_STATE(active),
        changed && "rule-changed",
      )}
    >
      <span className="py-2.5 pl-1">
        {also ? <span className="text-graphite">Also part of</span> : <Status r={r} />}
      </span>
      <button
        type="button"
        data-row=""
        aria-current={active ? "true" : undefined}
        className="grid min-w-0 grid-cols-[minmax(0,1fr)_4.5rem] items-baseline gap-3 py-2.5 text-left"
      >
        <span className="min-w-0">
          <span className="block text-[0.875rem] leading-snug font-medium text-ink">{r.name}</span>
          {summary ? (
            <span className="mt-0.5 block text-[0.8125rem] leading-snug text-graphite">
              {summary}
            </span>
          ) : null}
        </span>
        <span className="text-right font-mono text-xs text-graphite">{r.rule_id}</span>
      </button>
    </li>
  );
}

const WAIT = "wait:";
const WAIT_RULE = "|";
const waitStop = (key: string, ruleId?: string) => WAIT + key + (ruleId ? WAIT_RULE + ruleId : "");
// The element a stop scrolls to and focuses: a rule row keeps its #rule-ID anchor
const stopId = (stop: string) => (stop.startsWith(WAIT) ? stop : `rule-${stop}`);
// Every row on the page hovers, selects and marks itself the same way
const ROW_STATE = (active: boolean) =>
  active ? "bg-surface shadow-[inset_2px_0_0_var(--interstate)]" : "hover:bg-surface/60";

/** What the documents already settle for a rule that waits: its reason after the missing part
 * ("No driver exceeds 6 points..."), or its headline when the headline is not the missing part. */
function knownPart(r: Rule): string | null {
  if (!/unknown$/i.test(r.headline)) return r.headline;
  const rest = r.reason.startsWith(r.headline) ? r.reason.slice(r.headline.length) : "";
  return rest.replace(/^[.;:\s]+/, "") || null;
}

/** One missing answer and every rule waiting on it: a quiet heading, then the rules as ordinary
 * rows, each saying what is already known. */
function WaitingBlock({
  waitKey,
  group,
  row,
}: {
  waitKey: string;
  group: { label: string; blocks: Rule[]; adds: Rule[] };
  row: (
    r: Rule,
    stop: string,
    extra: { summary?: string | null; also?: boolean },
  ) => React.ReactNode;
}) {
  const asked = [...group.blocks, ...group.adds].some((r) => r.asked.length);
  return (
    <>
      <li className="px-2 pt-3 pb-1 text-[0.8125rem] text-graphite">
        Waiting on: {group.label}
        {asked ? " · asked in the agent request" : ""}
      </li>
      {group.blocks.map((r) => row(r, waitStop(waitKey, r.rule_id), { summary: knownPart(r) }))}
      {group.adds.map((r) => row(r, waitStop(waitKey, r.rule_id), { summary: null, also: true }))}
    </>
  );
}

function Settled({
  rules,
  row,
  selected,
}: {
  rules: Rule[];
  row: (r: Rule) => React.ReactNode;
  selected: string | null;
}) {
  const [open, setOpen] = useState(false);
  // A rule linked to from elsewhere opens the group it is in
  const [shownFor, setShownFor] = useState<string | null>(null);
  if (selected !== shownFor) {
    setShownFor(selected);
    if (rules.some((r) => r.rule_id === selected)) setOpen(true);
  }
  const passed = rules.filter((r) => r.kind === "pass").length;
  const na = rules.length - passed;
  return (
    <section className="mb-6">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex w-full items-baseline gap-2 border-y border-hairline px-1 py-2.5 text-left text-sm text-graphite hover:text-ink"
      >
        <span className="inline-block size-[7px] rounded-full bg-guide" aria-hidden="true" />
        {passed} passed{na ? `, ${na} not applicable` : ""}
        <span className="ml-auto text-interstate">{open ? "Hide" : "Show"}</span>
      </button>
      {open ? <ul>{rules.map(row)}</ul> : null}
    </section>
  );
}

/** One rule, always in the same order: what it checks, the line, what Lane found, what it
 * depends on, what it read, and the calls it rests on. A part shows only when it has content. */
function RuleDetail({
  r,
  labels,
  all,
  change,
}: {
  r: Rule;
  labels: Map<string, FactLabel>;
  all: Rule[];
  change?: Schemas["RuleChangeOut"];
}) {
  const sid = useSubmissionId();
  const reduced = useReducedMotion();
  const ruleset = useRuleset();
  // The fleet size above which the route changes team (a ruleset parameter)
  const small = ruleset.data?.params.small_fleet_max_units;
  const maxSmall = typeof small === "number" ? small : undefined;
  const blocking = new Set(r.blocking_conflicts);
  const facts = groupFacts(r.inputs, labels);
  return (
    <motion.div
      initial={reduced ? false : { opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.15 }}
      className="space-y-4 border-t border-hairline pt-4 xl:border-t-0 xl:border-l xl:pt-0 xl:pl-6"
    >
      <div>
        <p className="text-sm text-graphite">
          {r.rule_id}, {r.section.toLowerCase()}
        </p>
        <h2 className="mt-0.5 font-serif text-xl leading-snug font-medium">{r.name}</h2>
        <p className="mt-1 text-sm">
          <Status r={r} />
          {change ? (
            <span className="ml-2 text-graphite">
              was{" "}
              {change.potential_before
                ? "potential decline"
                : (OUTCOMES[change.before]?.label ?? change.before).toLowerCase()}
            </span>
          ) : null}
        </p>
      </div>

      <Part title="What Lane found">
        <p>{r.headline}.</p>
        {r.kind === "summary" ? (
          <RouteCauses all={all} />
        ) : extraItems(r).length ? (
          <ul className="mt-1 space-y-0.5 text-graphite">
            {extraItems(r).map((it) => (
              <li key={it}>{it}</li>
            ))}
          </ul>
        ) : null}
        {r.readings.length ? <Readings r={r} /> : null}
        {r.anchor_note ? <p className="mt-1 text-graphite">{r.anchor_note}</p> : null}
      </Part>

      {r.asked.length && r.kind !== "pass" && r.kind !== "n/a" ? (
        <Part title="Asked in the agent request">
          {/* Each request once, with the lines under it that concern this rule */}
          <ul className="space-y-1">
            {r.asked.map((ask) => (
              <li key={ask.text}>
                {ask.text}
                {ask.details.length ? (
                  <ul className="mt-0.5 space-y-0.5 pl-4 text-graphite">
                    {ask.details.map((d) => (
                      <li key={d} className="list-disc">
                        {d}
                      </li>
                    ))}
                  </ul>
                ) : null}
              </li>
            ))}
          </ul>
        </Part>
      ) : null}

      <Part title="The guideline says">
        <p className="font-serif text-[0.95rem] italic">&ldquo;{r.quote}&rdquo;</p>
      </Part>

      {/* The reasoning behind it, one click away so the panel opens on what matters */}
      <details className="group border-t border-hairline pt-3">
        <summary className="cursor-pointer text-sm text-interstate hover:underline">
          Why, and what it&rsquo;s based on
        </summary>
        <div className="mt-3 space-y-4">
          {r.explain ? (
            <Part title="What it checks">
              <p>{r.explain}</p>
            </Part>
          ) : null}

          {r.waiting_on.length ? (
            <Part title="Still unknown">
              {r.waiting_on.map((w) => (
                <p key={w.key}>{w.label}.</p>
              ))}
              <p className="text-graphite">
                {r.kind === "unknown"
                  ? "This rule is decided once the agent answers."
                  : "It does not change this outcome, but the rule is not fully checked until it is known."}
                {r.asked.length ? " It is in the agent request." : ""}
              </p>
            </Part>
          ) : null}

          {sameResult(r) || changes(r) ? (
            <Part title="What would change it">
              {sameResult(r) ? (
                <p>
                  Every count of {lowerWords(r.sensitivity[0].field)} the documents give leads to{" "}
                  {sameResult(r)}.
                  {r.sensitivity[0].field === "Power units" && maxSmall !== undefined
                    ? ` Only ${maxSmall + 1} or more power units would change it.`
                    : ""}
                </p>
              ) : (
                r.sensitivity.map((s, i) => (
                  <p key={i}>
                    If {whose(s)} is {String(s.value)}:{" "}
                    <span className="font-medium">{s.result ?? statusWord(s.outcome)}</span>
                  </p>
                ))
              )}
              {r.blocking_conflicts.length ? (
                <Link
                  href={`/submissions/${sid}/conflicts`}
                  className="mt-1 inline-block text-interstate hover:underline"
                >
                  Resolve the conflict on the Conflicts tab
                </Link>
              ) : null}
            </Part>
          ) : null}

          {r.inputs.length || r.evidence.length || r.absent.length ? (
            <Part title="Based on">
              {r.inputs.length ? (
                // Every fact it read, by record: the labels open the evidence
                <ul className="space-y-1">
                  {facts.map(([entity, fids]) => (
                    <li key={entity}>
                      <span className="text-graphite">{entity}: </span>
                      {fids.map((fid, i) => (
                        <span key={fid}>
                          {i ? ", " : ""}
                          <EvidenceLink
                            target={{
                              kind: "field",
                              fieldId: fid,
                              label: factText(labels.get(fid)),
                            }}
                            className={clsx(blocking.has(fid) && "font-medium text-warning")}
                          >
                            {labels.get(fid)?.field ?? "Fact"}
                          </EvidenceLink>
                        </span>
                      ))}
                    </li>
                  ))}
                </ul>
              ) : null}
              {r.evidence.length ? <ReadLines ids={r.evidence} name={r.name} /> : null}
              {r.absent.length && r.kind !== "pass" && r.kind !== "n/a" ? (
                // What the rule looked for and did not find; on a rule that holds it changed nothing
                <p className="text-graphite">Not in the submission: {r.absent.join(", ")}</p>
              ) : null}
            </Part>
          ) : null}

          {r.assumptions.length ? (
            <Part title="Assumptions it relies on">
              <Assumptions ids={r.assumptions} />
            </Part>
          ) : null}
        </div>
      </details>
    </motion.div>
  );
}

// Outcomes in the status names the page uses everywhere
const STATUS_WORD: Record<string, string> = {
  decline: "Decline",
  refer: "Refer",
  unknown: "Can’t check yet",
  flag: "Note",
  pass: "Pass",
  "n/a": "Not applicable",
};
const statusWord = (o: string) => STATUS_WORD[o] ?? o;

/** A disputed value changes this rule only when the values lead to different outcomes. */
function changes(r: Rule): boolean {
  return new Set(r.sensitivity.map((s) => s.result ?? s.outcome)).size > 1;
}

/** Whose value a "what would change it" line is about: "Pat Lee's date of birth", "the cargo
 * 2024-25 carrier"; a field of the one applicant or fleet needs no owner. Values keep their own
 * casing. */
function whose(s: Schemas["SensitivityOut"]): string {
  const field = lowerWords(s.field);
  if (!s.subject) return field;
  if (s.subject_kind === "driver") return `${s.subject}'s ${field}`;
  return `the ${s.subject.charAt(0).toLowerCase()}${s.subject.slice(1)} ${field}`;
}

/** The document lines a rule read from stored records (loss runs, MVRs), one line per document
 * with how many it read there; each opens those lines in the evidence lens. */
function ReadLines({ ids, name: ruleName }: { ids: string[]; name: string }) {
  const facts = useFacts(ids);
  const name = useSourceName(useSubmissionId());
  // One entry per document page: its distinct lines, at most three shown
  const byDoc = new Map<string, { ids: string[]; quotes: string[] }>();
  for (const f of facts.data ?? []) {
    const doc = name(f.document_title, f.document_id, f.cell_ref ? [`cell ${f.cell_ref}`] : []);
    const where = f.page_no ? `${doc}, p. ${f.page_no}` : doc;
    const entry = byDoc.get(where) ?? { ids: [], quotes: [] };
    entry.ids.push(f.id);
    const quote = String(f.quote ?? f.value ?? "").trim();
    if (quote && !entry.quotes.includes(quote)) entry.quotes.push(quote);
    byDoc.set(where, entry);
  }
  if (!byDoc.size) return null;
  const docs = [...byDoc.entries()].sort(([a], [b]) => docOrder(a, b));
  return (
    <ul className="mt-1 space-y-1.5">
      {docs.map(([where, d]) => (
        <li key={where}>
          <EvidenceLink target={{ kind: "facts", factIds: d.ids, label: `${ruleName}: ${where}` }}>
            {where}
          </EvidenceLink>
          <span className="block text-graphite">
            {d.quotes.slice(0, 3).join("; ")}
            {d.quotes.length > 3 ? `; and ${d.quotes.length - 3} more` : ""}
          </span>
        </li>
      ))}
    </ul>
  );
}

const LINE_ORDER = [/liability/i, /cargo/i, /physical damage/i];

/** Documents by line of coverage, then term ("2023-24"), then title. */
function docOrder(a: string, b: string): number {
  const line = (t: string) => {
    const i = LINE_ORDER.findIndex((re) => re.test(t));
    return i < 0 ? LINE_ORDER.length : i;
  };
  const term = (t: string) => /(\d{4})-\d{2}/.exec(t)?.[1] ?? "";
  return line(a) - line(b) || term(a).localeCompare(term(b)) || a.localeCompare(b);
}

/** Lower-case a label for the middle of a sentence, keeping acronyms: "IFTA miles" stays. */
function lowerWords(text: string): string {
  return text
    .split(" ")
    .map((w) => (/^[A-Z0-9]{2,}$/.test(w) ? w : w.toLowerCase()))
    .join(" ");
}

/** When every value the documents give leads to the same result (the team for every count of
 * power units), that result, so the rule can say what it decided instead of "Pass". */
function sameResult(r: Rule): string | null {
  const results = r.sensitivity.map((s) => s.result);
  return results.length && results.every((x) => x && x === results[0]) ? results[0]! : null;
}

/** Item lines that add something: a name the verdict already gives is not repeated. */
function extraItems(r: Rule): string[] {
  return r.items.filter((it) => !r.headline.includes(it));
}

/** The final route, by cause: what decides it, then each missing answer and the rules it holds,
 * then the checks that wait for the quote. */
function RouteCauses({ all }: { all: Rule[] }) {
  const needs = all.filter((x) => NEEDS.has(x.kind));
  const waiting = waitingGroups(all);
  const checks = all.filter((x) => x.kind === "check");
  return (
    <div className="mt-2 space-y-2 text-graphite">
      {needs.length ? (
        <div>
          <p className="text-ink">Decides the route</p>
          <ul className="space-y-0.5">
            {needs.map((x) => (
              <li key={x.rule_id}>
                {kindOf(x).word}: {x.name} <span className="font-mono text-xs">({x.rule_id})</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {waiting.length ? (
        <div>
          <p className="text-ink">Can&rsquo;t check yet</p>
          <ul className="space-y-0.5">
            {waiting.map(([key, g]) => (
              <li key={key}>
                {g.label}: {g.blocks.map((x) => x.name).join(", ")}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {checks.length ? (
        <div>
          <p className="text-ink">Checked at quote</p>
          <p>{checks.map((x) => x.name).join(", ")}</p>
        </div>
      ) : null}
    </div>
  );
}

function Part({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="text-sm">
      <h3 className="mb-1 text-[0.8125rem] font-medium text-graphite">{title}</h3>
      {children}
    </section>
  );
}

/** Every other defensible reading of the guideline line, one line each. */
function Readings({ r }: { r: Rule }) {
  return (
    <ul className="mt-1 space-y-0.5">
      {r.readings.map((rd) => (
        <li key={rd.id} className="text-graphite">
          Read as {lowerWords(rd.label)}
          {rd.assumption ? ` (${rd.assumption})` : ""}:{" "}
          <span className="text-ink">{statusWord(rd.outcome)}</span>, {rd.reason}
          {!rd.plausible ? ". Not a plausible reading here, so it never declines" : ""}
        </li>
      ))}
    </ul>
  );
}

/** The interpretive calls a rule rests on, written out. */
function Assumptions({ ids }: { ids: string[] }) {
  const ruleset = useRuleset();
  const text = ruleset.data?.assumptions ?? {};
  return (
    <ul className="space-y-1.5">
      {ids.map((a) => (
        <li key={a} className="text-graphite">
          {text[a] ?? a} <span className="font-mono text-xs">({a})</span>
        </li>
      ))}
    </ul>
  );
}
