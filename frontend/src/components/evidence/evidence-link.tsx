"use client";

import clsx from "clsx";

import { type EvidenceTarget, useLens } from "@/components/evidence/lens";

/**
 * A value that opens its evidence. Click, or focus and press E. The dotted underline is the only
 * decoration: every number in Lane can be traced, so the affordance stays quiet.
 */
export function EvidenceLink({
  target,
  children,
  className,
  title,
}: {
  target: EvidenceTarget;
  children: React.ReactNode;
  className?: string;
  title?: string;
}) {
  const lens = useLens();
  const active = lens.target && JSON.stringify(lens.target) === JSON.stringify(target);
  return (
    <button
      type="button"
      data-evidence=""
      title={title ?? "Show evidence (E)"}
      onClick={() => lens.open(target)}
      className={clsx(
        "cursor-pointer rounded-[4px] text-left underline decoration-graphite/40 decoration-dotted underline-offset-[3px] hover:text-interstate hover:decoration-interstate",
        active && "bg-interstate-soft text-interstate decoration-interstate",
        className,
      )}
    >
      {children}
    </button>
  );
}

/** A footnote marker ("3" or "1–9") that opens the facts behind it. Set inside a <sup>, so it
 * sits on the superscript line with no offset of its own. */
export function Footnote({ n, target }: { n: number | string; target: EvidenceTarget }) {
  const lens = useLens();
  return (
    <button
      type="button"
      data-evidence=""
      onClick={() => lens.open(target)}
      title={`Sources ${n}`}
      className="rounded-[3px] px-[0.15em] font-semibold text-interstate tabular-nums hover:bg-interstate-soft hover:underline"
    >
      {n}
    </button>
  );
}
