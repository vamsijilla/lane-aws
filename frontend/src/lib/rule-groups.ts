import type { Schemas } from "@/lib/api";

type Rule = Schemas["RuleOut"];

// Each kind (the API's, the same as the brief's) as a word, its colour and its group
export const KIND: Record<string, { word: string; text: string; dot: string }> = {
  potential_decline: { word: "Potential decline", text: "text-stop", dot: "bg-stop" },
  decline: { word: "Decline", text: "text-stop", dot: "bg-stop" },
  missing_document: { word: "Missing", text: "text-warning", dot: "bg-warning" },
  refer: { word: "Refer", text: "text-warning", dot: "bg-warning" },
  unknown: { word: "Can’t check yet", text: "text-unknown", dot: "bg-unknown" },
  check: { word: "At quote", text: "text-note", dot: "bg-note" },
  flag: { word: "Note", text: "text-note", dot: "bg-note" },
  pass: { word: "Pass", text: "text-guide", dot: "bg-guide" },
  "n/a": { word: "Not applicable", text: "text-note", dot: "bg-note" },
  summary: { word: "Route", text: "text-graphite", dot: "bg-graphite" },
};
export const kindOf = (r: Rule) => KIND[r.kind] ?? KIND.refer;
export const SEVERITY = ["decline", "potential_decline", "missing_document", "refer"];
export const NEEDS = new Set(SEVERITY);

/** Unknown rules, one block per missing fact; a rule that waits on nothing named stands alone. */
export function waitingGroups(rules: Rule[]) {
  const groups = new Map<string, { label: string; blocks: Rule[]; adds: Rule[] }>();
  for (const r of rules) {
    const waits = r.waiting_on.length
      ? r.waiting_on
      : r.kind === "unknown"
        ? [{ key: `rule:${r.rule_id}`, label: r.headline }]
        : [];
    for (const w of waits) {
      const g = groups.get(w.key) ?? { label: w.label, blocks: [], adds: [] };
      // An unknown rule waits on it; a rule that already refers or declines only adds it
      if (r.kind === "unknown") g.blocks.push(r);
      else g.adds.push(r);
      groups.set(w.key, g);
    }
  }
  return [...groups.entries()].filter(([, g]) => g.blocks.length);
}

/** How the rules stand, in the Rules page's groups: "7 need you, 7 can't check yet, 5 notes,
 * 5 passed, 4 not applicable". The final route sums them up and is not counted. */
export function groupCounts(rules: Rule[]): string {
  const n = (kinds: string[]) => rules.filter((r) => kinds.includes(r.kind)).length;
  return [
    [n([...SEVERITY]), "need you", "needs you"],
    [n(["unknown", "check"]), "can't check yet", "can't check yet"],
    [n(["flag"]), "notes", "note"],
    [n(["pass"]), "passed", "passed"],
    [n(["n/a"]), "not applicable", "not applicable"],
  ]
    .filter(([count]) => count)
    .map(([count, many, one]) => `${count} ${count === 1 ? one : many}`)
    .join(", ");
}
