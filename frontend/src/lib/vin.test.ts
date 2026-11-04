import { describe, expect, it } from "vitest";

import { checkDigit, modelYears, segments } from "@/lib/vin";

describe("vin", () => {
  it("works out the check digit", () => {
    expect(checkDigit("1M8GDM9AXKP042788")).toBe("X");
    expect(checkDigit("11111111111111111")).toBe("1");
    expect(checkDigit("1ABCD23EF4GH5678")).toBeNull(); // 16 characters
  });
  it("names each part", () => {
    expect(segments("1FUJGLDR9CLBP8834").map((s) => s.text)).toEqual([
      "1FU", "JGLDR", "9", "C", "L", "BP8834",
    ]); // prettier-ignore
    expect(modelYears("C")).toEqual([1982, 2012]);
  });
});
