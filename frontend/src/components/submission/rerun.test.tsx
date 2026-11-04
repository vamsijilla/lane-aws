import { act, render, screen, waitFor } from "@testing-library/react";
import { Toaster } from "sonner";
import { describe, expect, it, vi } from "vitest";

import { LastRerun, type Rerun, RerunProvider, useRerun } from "./rerun";

const rerun: Rerun = {
  run_id: "r1",
  what: "Owner drives resolved",
  headline_before: "Refer to Small Fleet underwriting",
  headline_after: "Refer to Small Fleet underwriting",
  potential_before: true,
  potential_after: true,
  changes: [
    {
      rule_id: "DRV-01",
      title: "Every driver holds a valid CDL",
      before: "refer",
      after: "pass",
      potential_before: true,
      potential_after: false,
    },
  ],
};

function Publisher() {
  const { publish } = useRerun();
  return (
    <button type="button" onClick={() => publish(rerun)}>
      Resolve
    </button>
  );
}

describe("rerun panel", () => {
  it("shows what the last decision changed until it is dismissed", () => {
    render(
      <RerunProvider>
        <Publisher />
        <LastRerun />
      </RerunProvider>,
    );
    expect(screen.queryByText(/Rules ran again/)).toBeNull();
    act(() => screen.getByRole("button", { name: "Resolve" }).click());
    expect(screen.getByText("Rules ran again after owner drives resolved")).toBeTruthy();
    expect(screen.getByText(/1 outcome changed/)).toBeTruthy();
    expect(screen.getByText(/still a potential decline/)).toBeTruthy();
    expect(screen.getByText("DRV-01")).toBeTruthy();
    act(() => screen.getByRole("button", { name: "Dismiss" }).click());
  });

  it("confirms a decision in a toast whose Undo reverses it and says so", async () => {
    const reverse = vi.fn(() =>
      Promise.resolve({ ...rerun, run_id: "r2", changes: [], potential_after: true }),
    );
    function Decide() {
      const { publish } = useRerun();
      return (
        <button
          type="button"
          onClick={() => publish({ ...rerun, undo: reverse, undone: "Owner drives reopened" })}
        >
          Resolve
        </button>
      );
    }
    render(
      <RerunProvider>
        <Decide />
        <LastRerun />
        <Toaster />
      </RerunProvider>,
    );
    act(() => screen.getByRole("button", { name: "Resolve" }).click());
    const undo = await screen.findByRole("button", { name: "Undo" });
    expect(screen.getAllByText("Owner drives resolved").length).toBeGreaterThan(0);
    act(() => undo.click());
    await waitFor(() => expect(reverse).toHaveBeenCalledOnce());
    expect(await screen.findByText("Rules ran again after owner drives reopened")).toBeTruthy();
  });

  it("cannot be used outside a submission", () => {
    expect(() => render(<Publisher />)).toThrow("useRerun outside RerunProvider");
  });
});
