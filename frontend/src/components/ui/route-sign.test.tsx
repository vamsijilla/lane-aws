import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RouteSign } from "./route-sign";

describe("RouteSign", () => {
  it("names the recommendation for assistive technology and shows only real lines", () => {
    render(
      <RouteSign
        outcome="refer"
        headline="Refer to Small Fleet underwriting"
        lines={["Potential decline: no CDL", null, "12 items needed"]}
      />,
    );
    const sign = screen.getByRole("status", {
      name: "Recommendation: Refer to Small Fleet underwriting",
    });
    expect(sign.dataset.outcome).toBe("refer");
    expect(sign.querySelectorAll("li")).toHaveLength(2); // the two real lines
  });

  it("draws small sizes as text with one status mark, a decline line in the stop tone", () => {
    render(<RouteSign size="sm" outcome="refer" headline="Refer" lines={["Potential decline"]} />);
    const sign = screen.getByRole("status");
    expect(sign.querySelectorAll("span.rounded-full")).toHaveLength(1); // the status mark only
    expect(sign.querySelector("span.bg-warning")).not.toBeNull();
    expect(screen.getByText("Potential decline").className).toContain("text-stop");
  });

  it("falls back to the pending tone for an unknown outcome", () => {
    render(<RouteSign size="xs" outcome="mystery" headline="Waiting" />);
    expect(screen.getByRole("status").innerHTML).toContain("bg-interstate");
  });
});
