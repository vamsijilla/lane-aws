"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { LensPanel } from "@/components/evidence/lens-panel";

import type { Schemas } from "@/lib/api";

/** A value a figure or rule was worked out from, opening its own source. */
export type LensInput = { label: string; target: EvidenceTarget };

/** The submission tab that holds a cited rule, conflict or missing item, and its row there. */
export type LensPanelLink = {
  tab: "rules" | "conflicts" | "missing" | "documents";
  anchor?: string;
  label: string;
};

export type EvidenceTarget =
  | { kind: "field"; fieldId: string; label?: string }
  | {
      kind: "facts";
      factIds: string[];
      label: string;
      note?: string;
      inputs?: LensInput[];
      panel?: LensPanelLink;
      empty?: string; // what to say when no fact is behind it
    }
  | { kind: "entity"; entityIds: string[]; label: string }
  | { kind: "page"; pageId: string; label: string; boxes?: number[][]; note?: string }
  // A run of footnotes ("19–24"): each one opens by what it cites
  | {
      kind: "refs";
      label: string;
      notes: Schemas["FootnoteOut"][];
      refs: Record<string, Schemas["RefOut"] | undefined>;
    };

/** One source to open, not a run of them. */
export type SingleTarget = Exclude<EvidenceTarget, { kind: "refs" }>;

type Lens = {
  open: (target: EvidenceTarget) => void;
  close: () => void;
  target: EvidenceTarget | null;
};

const LensContext = createContext<Lens | null>(null);

export function useLens(): Lens {
  const lens = useContext(LensContext);
  if (!lens) throw new Error("useLens outside EvidenceProvider");
  return lens;
}

/**
 * The evidence lens. Any value in a submission opens it; it shows where the value came from on
 * the source page, how it was read and how sure the system is. E opens it on the focused value,
 * Esc closes it, Left and Right move between sources.
 */
export function EvidenceProvider({
  submissionId,
  children,
}: {
  submissionId: string;
  children: React.ReactNode;
}) {
  const [target, setTarget] = useState<EvidenceTarget | null>(null);
  const close = useCallback(() => setTarget(null), []);
  const value = useMemo(() => ({ open: setTarget, close, target }), [close, target]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const typing = e.target instanceof HTMLElement && e.target.closest("input, textarea, select");
      if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
      if (e.key === "Escape" && target) {
        e.preventDefault();
        setTarget(null);
      } else if (e.key === "e" || e.key === "E") {
        const el = document.activeElement;
        if (el instanceof HTMLElement && el.dataset.evidence !== undefined) {
          e.preventDefault();
          el.click();
        }
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [target]);

  return (
    <LensContext.Provider value={value}>
      {children}
      <LensPanel submissionId={submissionId} target={target} onClose={close} />
    </LensContext.Provider>
  );
}
