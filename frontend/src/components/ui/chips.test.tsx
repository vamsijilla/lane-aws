import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { OutcomeChip, StateChip } from "./chips";

describe("chips", () => {
  it("shows a potential decline over the rule's own outcome", () => {
    render(<OutcomeChip outcome="refer" potential />);
    expect(screen.getByText("Potential decline")).toBeTruthy();
    expect(screen.queryByText("Refer")).toBeNull();
  });

  it("falls back to unknown for an outcome it does not know", () => {
    render(<OutcomeChip outcome="maybe" />);
    expect(screen.getByText("Can’t check yet")).toBeTruthy();
  });

  it("draws a missing value as an empty slot and counts conflicting values", () => {
    const { container } = render(<StateChip state="missing" />);
    expect(screen.getByText("Not provided")).toBeTruthy();
    expect(container.querySelector(".border-dashed")).not.toBeNull();
    render(<StateChip state="conflict" count={3} />);
    expect(screen.getByText("Conflict, 3 values")).toBeTruthy();
  });
});
