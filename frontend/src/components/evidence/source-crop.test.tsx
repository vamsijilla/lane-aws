import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Schemas } from "@/lib/api";

import { SourceCrop } from "./source-crop";

const cellFact = {
  id: "f1",
  page_id: "p1",
  page_no: 2,
  cell_ref: "'Application'!E50",
  quote: "SECRET-CELL-VALUE",
  value: "SECRET-CELL-VALUE",
  bbox: null,
} as unknown as Schemas["EvidenceFact"];

describe("source crop", () => {
  it("shows no cell value or instruction as text; both are in the accessible name", () => {
    const { container } = render(<SourceCrop fact={cellFact} page={undefined} onOpen={() => {}} />);
    expect(container.textContent).toBe("");
    const button = screen.getByRole("button");
    const name = button.getAttribute("aria-label") ?? "";
    expect(name).toContain("Open where this was read, page 2");
    expect(name).toContain("cell E50");
    expect(name).toContain("SECRET-CELL-VALUE");
  });

  it("names a page crop for screen readers without visible text", () => {
    const fact = {
      ...cellFact,
      cell_ref: null,
      bbox: [10, 10, 50, 20],
    } as unknown as Schemas["EvidenceFact"];
    const page = {
      id: "p1",
      width: 600,
      height: 800,
      has_image: true,
      personal_photo: false,
    } as unknown as Schemas["PageOut"];
    const { container } = render(<SourceCrop fact={fact} page={page} onOpen={() => {}} />);
    expect(container.textContent).toBe("");
    expect(screen.getByRole("button").getAttribute("aria-label")).toBe(
      "Open where this was read, page 2",
    );
  });
});
