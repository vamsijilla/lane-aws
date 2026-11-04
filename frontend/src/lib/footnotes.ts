import type { EvidenceTarget } from "@/components/evidence/lens";
import { type Refs, refTarget } from "@/components/evidence/targets";
import type { Schemas } from "@/lib/api";

export type Footnote = Schemas["FootnoteOut"];

/** Runs of consecutive footnote numbers: [1,2,3,7] -> [[1,2,3],[7]]. */
export function clusters(ns: number[]): number[][] {
  const out: number[][] = [];
  for (const n of [...new Set(ns)].sort((a, b) => a - b)) {
    const last = out[out.length - 1];
    if (last && n === last[last.length - 1] + 1) last.push(n);
    else out.push([n]);
  }
  return out;
}

/** How a run is labelled: "3", "3,4" or "3–7". */
export function clusterLabel(run: number[]): string {
  return run.length > 2 ? `${run[0]}–${run[run.length - 1]}` : run.join(",");
}

/** One footnote opens by what it cites; a run opens each of its footnotes in turn, never
 * nothing, so every number in the summary opens a source. */
export function clusterTarget(
  run: number[],
  notes: Map<number, Footnote>,
  refs: Refs = {},
): EvidenceTarget | null {
  const listed = run.flatMap((n) => {
    const f = notes.get(n);
    return f ? [f] : [];
  });
  if (!listed.length) return null;
  if (listed.length === 1) return refTarget(listed[0], `Source ${listed[0].n}`, refs);
  return { kind: "refs", label: `Sources ${clusterLabel(run)}`, notes: listed, refs };
}

export type Topic = "account" | "decision" | "risk" | "losses" | "operations" | "gaps";

const LOSSES = /\b(claims?|loss(es)?|incurred|reserved?|paid)\b/i;
const OPERATIONS =
  /\b(miles?|mileage|ifta|territor(y|ies)|radius|operations|telematics|states?)\b/i;

/** What a sentence is about, read from what it cites (never its wording): the opening
 * describes the account; rules make the decision; missing items are gaps and next steps;
 * claims and losses, and mileage and territory, are their own topics; the rest are signals. */
export function topicOf(footnotes: number[], notes: Map<number, Footnote>, first: boolean): Topic {
  const cited = footnotes.flatMap((n) => {
    const f = notes.get(n);
    return f ? [f] : [];
  });
  if (first) return "account";
  if (!cited.length) return "risk";
  const share = (test: (f: Footnote) => boolean) => cited.filter(test).length / cited.length;
  // Missing items or rules, when cited more than any other kind of source
  const count = (kind: string) => cited.filter((f) => f.kind === kind).length;
  const most = Math.max(...[...new Set(cited.map((f) => f.kind))].map(count));
  if (count("missing") === most) return "gaps";
  if (count("rule") === most) return "decision";
  if (share((f) => LOSSES.test(f.label)) >= 0.5) return "losses";
  if (share((f) => OPERATIONS.test(f.label)) >= 0.5) return "operations";
  return "risk";
}

/** Sentences in order, broken into paragraphs where the topic changes. Same sentences, same
 * order; only the breaks are added. */
export function paragraphs<S extends { footnotes: number[] }>(
  sentences: S[],
  notes: Map<number, Footnote>,
): S[][] {
  const out: S[][] = [];
  let last: Topic | null = null;
  sentences.forEach((s, i) => {
    const topic = topicOf(s.footnotes, notes, i === 0);
    if (topic !== last || !out.length) out.push([s]);
    else out[out.length - 1].push(s);
    last = topic;
  });
  return out;
}
