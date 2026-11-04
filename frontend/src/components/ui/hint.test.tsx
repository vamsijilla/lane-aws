import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Hint, HintProvider } from "./hint";

describe("Hint", () => {
  it("opens on keyboard focus, not only on hover, and shows its text", async () => {
    render(
      <HintProvider>
        <Hint content="An underwriter must review this before a quote.">Refer</Hint>
      </HintProvider>,
    );
    const trigger = screen.getByText("Refer");
    expect(trigger.tabIndex).toBe(0); // reachable by keyboard
    await act(async () => {
      fireEvent.focus(trigger);
    });
    expect((await screen.findByRole("tooltip")).textContent).toBe(
      "An underwriter must review this before a quote.",
    );
  });

  it("renders the text alone when there is nothing to explain", () => {
    render(<Hint content={undefined}>Pass</Hint>);
    expect(screen.getByText("Pass").tabIndex).toBe(-1);
  });
});
