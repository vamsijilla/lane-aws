import { describe, expect, it } from "vitest";

import { fieldName } from "./documents";

describe("fieldName", () => {
  it("says a catalog path the way a person would", () => {
    expect(fieldName("fein")).toBe("FEIN");
    expect(fieldName("dot_number")).toBe("USDOT number");
    expect(fieldName("agency.name")).toBe("agency name");
    expect(fieldName("drivers[].license_number")).toBe("drivers license number");
  });
});
