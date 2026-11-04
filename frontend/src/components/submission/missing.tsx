"use client";

import { CircleCheckBig, CircleDashed } from "lucide-react";

import { useSubmissionId } from "@/components/submission/shell";
import { Chip } from "@/components/ui/chips";
import { Empty, ErrorNote, Panel, Skeleton } from "@/components/ui/primitives";
import { titleCase } from "@/lib/format";
import { useActions, useMissing, useRules, useRuleset, useSetItemIncluded } from "@/lib/queries";
import { useSession } from "@/lib/session";

const CATEGORY: Record<string, string> = {
  application: "Application",
  operations: "Operations questions",
  vehicles: "Vehicles",
  drivers: "Drivers",
  loss_history: "Loss history and prior insurance",
};

const TIERS = [
  {
    key: "required",
    title: "Required before quote",
    note: "The guideline asks for these before a quote. They are in the agent request.",
  },
  {
    key: "bind",
    title: "Needed to bind",
    note: "Asked for with the items needed before binding.",
  },
  {
    key: "helpful",
    title: "Also helpful",
    note: "Form blanks that help but are not required. Add them to the agent request if you want them.",
  },
] as const;

export function Missing() {
  const id = useSubmissionId();
  const missing = useMissing(id);
  const actions = useActions(id);
  const rules = useRules(id);
  const ruleset = useRuleset();
  const toggle = useSetItemIncluded(id);
  const session = useSession();
  if (missing.isPending) return <Skeleton lines={10} />;
  if (missing.error)
    return <ErrorNote title="Missing items could not be loaded">{missing.error.message}</ErrorNote>;
  if (!missing.data.length)
    return (
      <Empty icon={CircleCheckBig} title="Nothing is missing">
        Every item the guideline asks for was sent.
      </Empty>
    );

  const items = actions.data?.items ?? [];
  const itemFor = (missingId: string) => items.find((i) => i.source_refs.includes(missingId));
  // A missing item a rule's request already asks for lives under that rule's request item.
  const coveringRule = (code: string) =>
    ruleset.data?.rules.find((r) => r.covers.some((prefix) => code.startsWith(prefix)))?.id;
  const byCategory = (list: typeof missing.data) => {
    const groups = new Map<string, typeof missing.data>();
    for (const m of list) groups.set(m.category, [...(groups.get(m.category) ?? []), m]);
    return groups;
  };
  const quotes = new Map((rules.data?.rules ?? []).map((r) => [r.rule_id, r.quote]));

  return (
    <div className="space-y-4">
      <p className="text-graphite">
        {missing.data.length} things the submission does not contain, in plain underwriting terms.
        Required items are in the agent request; helpful ones are added only if you choose.
      </p>
      {TIERS.map((t) => {
        const tierItems = missing.data.filter((m) => m.tier === t.key);
        if (!tierItems.length) return null;
        return (
          <section key={t.key} aria-labelledby={`tier-${t.key}`} className="space-y-3">
            <div>
              <h2 id={`tier-${t.key}`} className="font-display text-md font-semibold">
                {t.title} <span className="text-graphite tabular-nums">{tierItems.length}</span>
              </h2>
              <p className="text-sm text-graphite">{t.note}</p>
            </div>
            {[...byCategory(tierItems).entries()].map(([category, list]) => (
              <Panel key={category} title={CATEGORY[category] ?? titleCase(category)} flush>
                <ul>
                  {list.map((m) => {
                    const rule = coveringRule(m.code);
                    const item =
                      itemFor(m.id) ??
                      (rule ? items.find((i) => i.source_refs.includes(`R:${rule}`)) : undefined);
                    const ruleRef = item?.source_refs.find((r) => r.startsWith("R:"))?.slice(2);
                    return (
                      <li
                        key={m.id}
                        id={m.code}
                        className="scroll-mt-48 grid grid-cols-[1.25rem_minmax(0,1fr)_auto] items-start gap-3 border-b border-hairline px-4 py-3 last:border-0"
                      >
                        <CircleDashed
                          size={16}
                          className="mt-0.5 text-graphite"
                          aria-hidden="true"
                        />
                        {/* A cited missing item (a footnote, a finding) links here by its id */}
                        <div id={`missing-${m.id}`} className="min-w-0 scroll-mt-48">
                          <p className="font-semibold">{m.title}</p>
                          <p className="text-sm text-graphite">{m.detail}</p>
                          {ruleRef && quotes.get(ruleRef) ? (
                            <p className="mt-1 text-xs text-graphite">
                              Guideline, {ruleRef}: <q>{quotes.get(ruleRef)}</q>
                            </p>
                          ) : null}
                        </div>
                        {item ? (
                          <label className="flex shrink-0 items-center gap-2 text-sm">
                            <input
                              type="checkbox"
                              checked={item.included}
                              disabled={!session.can("review") || toggle.isPending}
                              onChange={(e) =>
                                toggle.mutate({ itemId: item.id, included: e.target.checked })
                              }
                              className="size-4 accent-[var(--interstate)]"
                            />
                            Include in agent request
                          </label>
                        ) : (
                          <Chip tone="neutral">Internal</Chip>
                        )}
                      </li>
                    );
                  })}
                </ul>
              </Panel>
            ))}
          </section>
        );
      })}
    </div>
  );
}
