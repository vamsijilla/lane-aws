import { afterEach, describe, expect, it, vi } from "vitest";

import {
  applyThemeChoice,
  nextThemeChoice,
  readThemeChoice,
  subscribeThemeChoice,
  THEME_STORAGE_KEY,
} from "./theme";

afterEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
});

describe("theme", () => {
  it("cycles system, light, dark", () => {
    expect(nextThemeChoice("system")).toBe("light");
    expect(nextThemeChoice("light")).toBe("dark");
    expect(nextThemeChoice("dark")).toBe("system");
  });

  it("applies an explicit choice to <html> and persists it", () => {
    applyThemeChoice("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
    expect(readThemeChoice()).toBe("dark");
  });

  it("system clears the attribute and storage", () => {
    applyThemeChoice("light");
    applyThemeChoice("system");
    expect(document.documentElement.hasAttribute("data-theme")).toBe(false);
    expect(readThemeChoice()).toBe("system");
  });

  it("ignores unknown stored values", () => {
    localStorage.setItem(THEME_STORAGE_KEY, "sepia");
    expect(readThemeChoice()).toBe("system");
  });

  it("notifies subscribers", () => {
    const listener = vi.fn();
    const unsubscribe = subscribeThemeChoice(listener);
    applyThemeChoice("dark");
    unsubscribe();
    applyThemeChoice("light");
    expect(listener).toHaveBeenCalledTimes(1);
  });
});
