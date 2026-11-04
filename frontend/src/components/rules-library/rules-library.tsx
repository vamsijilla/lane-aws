"use client";

import { OctagonX } from "lucide-react";

import { KnownLayouts } from "@/components/rules-library/known-layouts";
import { Chip } from "@/components/ui/chips";
import { ErrorNote, Identifier, PageTitle, Panel, Skeleton } from "@/components/ui/primitives";
import { useRuleset } from "@/lib/queries";

/** The guideline as rules: what each rule enforces, in the guideline's words, and its settings. */
export function RulesLibrary() {
  const ruleset = useRuleset();
  if (ruleset.isPending) {
    return (
      <div className="mx-auto max-w-6xl px-8 py-6">
        <Skeleton lines={12} />
      </div>
    );
  }
  if (ruleset.error) {
    return (
      <div className="mx-auto max-w-6xl px-8 py-6">
        <ErrorNote title="The ruleset could not be loaded">{ruleset.error.message}</ErrorNote>
      </div>
    );
  }
  const r = ruleset.data;
  const sections = [...new Set(r.rules.map((x) => x.section))];
  const params = Object.entries(r.params).filter(
    ([, v]) => typeof v !== "object" || Array.isArray(v),
  );
  return (
    <div className="mx-auto max-w-6xl px-8 py-6">
      <PageTitle
        title="Rules library"
        description={
          <>
            {r.rules.length} rules from {r.guideline}, version {r.version}. Fingerprint{" "}
            <Identifier value={r.sha256.slice(0, 12)} />. Underwriters change thresholds in the
            ruleset file, not in code.
          </>
        }
      />
      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1fr)_20rem]">
        <div className="space-y-6">
          {sections.map((s) => (
            <Panel key={s} title={s} flush>
              <ul>
                {r.rules
                  .filter((x) => x.section === s)
                  .map((x) => (
                    <li
                      key={x.id}
                      className="grid grid-cols-[4.5rem_minmax(0,1fr)] gap-3 border-b border-hairline px-4 py-2.5 last:border-0"
                    >
                      <span className="font-mono text-sm text-graphite">{x.id}</span>
                      <div>
                        <p className="flex flex-wrap items-center gap-2 font-semibold">
                          {x.title}
                          {x.decline ? (
                            <Chip tone="stop" icon={OctagonX}>
                              Can decline
                            </Chip>
                          ) : null}
                        </p>
                        <p className="mt-0.5 text-sm text-graphite">
                          <q>{x.quote}</q>
                        </p>
                        {x.request ? (
                          <p className="mt-0.5 text-sm">Asks the agent for: {x.request}</p>
                        ) : null}
                      </div>
                    </li>
                  ))}
              </ul>
            </Panel>
          ))}
          <KnownLayouts />
        </div>
        <aside className="space-y-6">
          <Panel title="Routing">
            <ul className="space-y-1 text-sm">
              {Object.entries(r.teams).map(([k, v]) => (
                <li key={k} className="flex justify-between gap-2">
                  <span className="text-graphite">{k.replace("_", " ")}</span>
                  <span className="font-semibold">{v}</span>
                </li>
              ))}
            </ul>
          </Panel>
          <Panel
            title="Assumptions"
            description="Where the guideline is silent or ambiguous. Rules cite these by id."
          >
            <dl className="space-y-2 text-sm">
              {Object.entries(r.assumptions).map(([id, text]) => (
                <div key={id}>
                  <dt className="font-mono text-xs text-unknown">{id}</dt>
                  <dd className="text-graphite">{text}</dd>
                </div>
              ))}
            </dl>
          </Panel>
          <Panel title="Settings">
            <dl className="space-y-2 text-sm">
              {params.map(([k, v]) => (
                <div key={k}>
                  <dt className="text-graphite">{settingLabel(k)}</dt>
                  <dd className="font-semibold break-words">
                    <SettingValue name={k} value={v} />
                  </dd>
                </div>
              ))}
            </dl>
          </Panel>
        </aside>
      </div>
    </div>
  );
}

const ACRONYMS = new Set(["ifta", "mvr", "cdl", "mph", "dot", "vin", "um", "pd"]);

/** 'mvr_max_age_days' -> 'MVR max age days'. */
function settingLabel(key: string): string {
  const words = key.split("_").map((w) => {
    if (ACRONYMS.has(w)) return w.toUpperCase();
    const span = /^(\d+)(y|m)$/.exec(w); // '3y' -> 'in 3 years', '12m' -> 'in 12 months'
    return span ? `in ${span[1]} ${span[2] === "y" ? "years" : "months"}` : w;
  });
  const text = words.join(" ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** A ruleset setting as people read it: a list of records as one line each, by its label. */
function SettingValue({ name, value }: { name: string; value: unknown }) {
  if (Array.isArray(value) && value.some((x) => x !== null && typeof x === "object")) {
    return (
      <ul className="space-y-0.5 font-normal">
        {value.map((item, i) => (
          <li key={i}>{recordLine(item)}</li>
        ))}
      </ul>
    );
  }
  if (Array.isArray(value)) return <>{value.map(String).join(", ")}</>;
  if (value !== null && typeof value === "object") return <>{recordLine(value)}</>;
  // A value that is a name in the ruleset ('application_date') reads as words
  if (typeof value === "string" && /^[a-z]+(_[a-z]+)+$/.test(value))
    return <>{settingLabel(value)}</>;
  if (typeof value === "number") return <>{numberWithUnit(name, value)}</>;
  return <>{String(value)}</>;
}

/** A number as its setting means it: a share as a percentage, a premium in dollars, a count
 * of days, years, hours or units with its unit. */
export function numberWithUnit(name: string, value: number): string {
  if (/(_share|_rate|_ratio)$/.test(name) && value <= 1) return `${Math.round(value * 1000) / 10}%`;
  if (/(^|_)units(_|$)/.test(name) && !/per_unit/.test(name))
    return `${value.toLocaleString("en-US")} ${value === 1 ? "unit" : "units"}`;
  if (/premium|_usd$|_limit$/.test(name)) return `$${value.toLocaleString("en-US")}`;
  const unit = /_(days|years|hours|units|miles|points|quarters)$/.exec(name)?.[1];
  const n = value.toLocaleString("en-US");
  if (!unit) return n;
  const word = value === 1 ? unit.replace(/s$/, "") : unit;
  return `${n} ${word}`;
}

function recordLine(item: unknown): string {
  if (item === null || typeof item !== "object") return String(item);
  const r = item as Record<string, unknown>;
  if (typeof r.label === "string") {
    return typeof r.years === "number" ? `${r.label}, within ${r.years} years` : r.label;
  }
  return Object.entries(r)
    .map(([k, v]) => `${k.replaceAll("_", " ")}: ${String(v)}`)
    .join("; ");
}
