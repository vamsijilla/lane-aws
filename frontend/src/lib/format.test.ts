import { describe, expect, it } from "vitest";

import { formatDate, formatQuarter } from "@/lib/format";

describe("one date and quarter format everywhere", () => {
  it("formats ISO and US dates as 'Feb 10, 2023'", () => {
    expect(formatDate("2023-02-10")).toBe("Feb 10, 2023");
    expect(formatDate("02/10/2023")).toBe("Feb 10, 2023");
    expect(formatDate("4/22/2025")).toBe("Apr 22, 2025");
    expect(formatDate("not a date")).toBe("not a date");
  });
  it("formats quarters as 'Q3 2025'", () => {
    expect(formatQuarter("2025-Q3")).toBe("Q3 2025");
    expect(formatQuarter("Q3 2025")).toBe("Q3 2025");
  });
});
