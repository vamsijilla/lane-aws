import { describe, expect, it } from "vitest";

import { clusterLabel, clusters, clusterTarget, type Footnote, paragraphs } from "./footnotes";

const note = (n: number, facts: string[], kind = "field"): Footnote => ({
  n,
  ref: `F${n}`,
  kind,
  label: `Label ${n}`,
  target: `field-${n}`,
  fact_ids: facts,
  document_ids: [],
  cites: [],
  value: null,
});

describe("footnote clusters", () => {
  it("groups consecutive numbers and sorts them", () => {
    expect(clusters([7, 1, 3, 2])).toEqual([[1, 2, 3], [7]]);
    expect(clusters([4, 4, 5])).toEqual([[4, 5]]);
    expect(clusters([])).toEqual([]);
  });

  it("labels a long run as a range", () => {
    expect(clusterLabel([3])).toBe("3");
    expect(clusterLabel([3, 4])).toBe("3,4");
    expect(clusterLabel([3, 4, 5, 6])).toBe("3–6");
  });

  it("opens a single field footnote on the field itself", () => {
    const notes = new Map([[1, note(1, ["a"])]]);
    expect(clusterTarget([1], notes)).toEqual({
      kind: "field",
      fieldId: "field-1",
      label: "Label 1",
    });
  });

  it("opens a run footnote by footnote", () => {
    const notes = new Map([
      [1, note(1, ["a", "b"])],
      [2, note(2, ["b", "c"])],
    ]);
    expect(clusterTarget([1, 2], notes)).toEqual({
      kind: "refs",
      label: "Sources 1,2",
      notes: [notes.get(1), notes.get(2)],
      refs: {},
    });
  });

  it("opens a run with no facts too, so no sentence loses its numbers", () => {
    const notes = new Map([
      [1, note(1, [], "rule")],
      [2, note(2, [], "missing")],
    ]);
    expect(clusterTarget([1, 2], notes)?.kind).toBe("refs");
  });
});

describe("a citation opens by its kind", () => {
  it("opens a figure with its working and its inputs", () => {
    const fig = {
      ...note(1, ["a"], "figure"),
      value: "128,609 (514,435 miles, 4 units)",
      cites: ["E2"],
    };
    const refs = { E2: { ...note(2, ["a"], "entity"), ref: "E2" } };
    const t = clusterTarget([1], new Map([[1, fig]]), refs);
    expect(t).toMatchObject({
      kind: "facts",
      note: "Worked out by code: 128,609 (514,435 miles, 4 units).",
      inputs: [{ label: "Label 2", target: { kind: "facts", factIds: ["a"] } }],
    });
  });

  it("opens a rule on the facts of the fields it read, with a way to the Rules tab", () => {
    const rule = { ...note(1, [], "rule"), target: "DOC-05", cites: ["F2"] };
    const refs = { F2: note(2, ["x", "y"]) };
    expect(clusterTarget([1], new Map([[1, rule]]), refs)).toMatchObject({
      kind: "facts",
      factIds: ["x", "y"],
      panel: { tab: "rules", anchor: "DOC-05" },
    });
  });

  it("opens a missing item on its panel and says nothing states it yet", () => {
    const m = { ...note(1, [], "missing"), target: "m1" };
    expect(clusterTarget([1], new Map([[1, m]]))).toMatchObject({
      panel: { tab: "missing", anchor: "missing-m1" },
      empty: "No document states this yet.",
    });
  });
});

describe("summary paragraphs", () => {
  const n = (i: number, kind: string, label: string): Footnote => ({ ...note(i, []), kind, label });
  const notes = new Map([
    [1, n(1, "field", "Applicant: Legal name")],
    [2, n(2, "rule", "ROUTE-01 Route")],
    [3, n(3, "rule", "DOC-02 Loss runs")],
    [4, n(4, "entity", "Claim A1")],
    [5, n(5, "figure", "Claims reported late")],
    [6, n(6, "missing", "Garaging address")],
  ]);
  const s = (fs: number[]) => ({ text: "x", footnotes: fs });

  it("breaks at topic changes and keeps every sentence in order", () => {
    const input = [s([1]), s([2]), s([3]), s([4]), s([5]), s([6])];
    const out = paragraphs(input, notes);
    expect(out.map((p) => p.length)).toEqual([1, 2, 2, 1]);
    expect(out.flat()).toEqual(input);
  });
});
