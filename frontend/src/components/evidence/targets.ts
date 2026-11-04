import type { LensInput, SingleTarget } from "@/components/evidence/lens";
import type { Schemas } from "@/lib/api";

type Ref = Schemas["RefOut"];
export type Refs = Record<string, Ref | undefined>;

/** Where a footnote or a finding citation opens the lens, by what it cites: a fact on its page,
 * a figure with its working and inputs, an entity where it appears, a rule, conflict or missing
 * item with a way to its panel. Null only for a citation that does not resolve. */
export function refTarget(
  ref: Ref | undefined,
  fallbackLabel: string,
  refs: Refs = {},
): SingleTarget | null {
  if (!ref) return null;
  const label = ref.label || fallbackLabel;
  const inputs = (): LensInput[] =>
    ref.cites.flatMap((c) => {
      const r = refs[c];
      const target = r ? refTarget(r, c, refs) : null;
      return r && target ? [{ label: r.label || c, target }] : [];
    });
  const facts = (extra: string[] = []) => [...new Set([...ref.fact_ids, ...extra])];
  switch (ref.kind) {
    case "field":
      return { kind: "field", fieldId: ref.target, label };
    case "entity":
      return { kind: "facts", factIds: facts(), label, note: "Where it appears in the documents." };
    case "figure":
      return {
        kind: "facts",
        factIds: facts(),
        label,
        note: ref.value ? `Worked out by code: ${ref.value}.` : "Worked out by code.",
        inputs: inputs(),
        empty: "No document value went into this figure.",
      };
    case "rule":
      return {
        kind: "facts",
        // A rule cites the fields it read; their facts are what it was decided on
        factIds: facts(ref.cites.flatMap((c) => refs[c]?.fact_ids ?? [])),
        label,
        note: "What this rule read.",
        inputs: inputs(),
        panel: { tab: "rules", anchor: ref.target, label: "Open on the Rules tab" },
        empty: "This rule read no document value.",
      };
    case "conflict":
      return {
        kind: "facts",
        factIds: facts(),
        label,
        note: "The documents disagree on this value.",
        inputs: inputs(),
        panel: {
          tab: "conflicts",
          anchor: `conflict-${ref.target}`,
          label: "Open on the Conflicts tab",
        },
      };
    case "missing":
      return {
        kind: "facts",
        factIds: facts(),
        label,
        note: "Still needed from the agent.",
        panel: {
          tab: "missing",
          anchor: `missing-${ref.target}`,
          label: "Open on the Missing tab",
        },
        empty: "No document states this yet.",
      };
    case "document":
      return {
        kind: "facts",
        factIds: facts(),
        label,
        panel: { tab: "documents", label: "Open on the Documents tab" },
        empty: "No fact was read from this document.",
      };
    default:
      return ref.fact_ids.length ? { kind: "facts", factIds: ref.fact_ids, label } : null;
  }
}
