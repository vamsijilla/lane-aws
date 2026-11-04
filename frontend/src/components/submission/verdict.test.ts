import { describe, expect, it } from "vitest";

import { verdictSentence } from "./overview";

describe("verdictSentence", () => {
  it("spells out a small opening count and joins the decline and the request", () => {
    expect(verdictSentence(["1 driver does not meet the CDL requirement"], 10)).toBe(
      "One driver does not meet the CDL requirement, and 10 items are still needed from the agent before a quote.",
    );
  });

  it("counts several declines instead of listing them", () => {
    expect(verdictSentence(["a", "b"], 0)).toBe("Two findings could decline it.");
  });

  it("says nothing when nothing stands in the way", () => {
    expect(verdictSentence([], 0)).toBeUndefined();
  });

  it("uses the singular for one item", () => {
    expect(verdictSentence([], 1)).toBe("One item is still needed from the agent before a quote.");
  });
});
