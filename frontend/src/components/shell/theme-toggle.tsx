"use client";

import { Monitor, Moon, Sun } from "lucide-react";
import { useSyncExternalStore } from "react";

import {
  applyThemeChoice,
  nextThemeChoice,
  readThemeChoice,
  subscribeThemeChoice,
  type ThemeChoice,
} from "@/lib/theme";

const LABEL: Record<ThemeChoice, string> = {
  system: "Theme: match system",
  light: "Theme: light",
  dark: "Theme: dark",
};

export function ThemeToggle() {
  const choice = useSyncExternalStore<ThemeChoice>(
    subscribeThemeChoice,
    readThemeChoice,
    () => "system",
  );
  const Icon = choice === "light" ? Sun : choice === "dark" ? Moon : Monitor;

  return (
    <button
      type="button"
      onClick={() => applyThemeChoice(nextThemeChoice(choice))}
      title={LABEL[choice]}
      aria-label={LABEL[choice]}
      className="inline-flex size-8 items-center justify-center rounded-[var(--radius-cell)] text-graphite hover:bg-surface-sunk hover:text-ink"
    >
      <Icon size={16} aria-hidden="true" />
    </button>
  );
}
