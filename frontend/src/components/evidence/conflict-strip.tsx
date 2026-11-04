"use client";

import clsx from "clsx";

import { useSourceName } from "@/lib/source-names";
import type { Schemas } from "@/lib/api";

const CANDIDATE_TONES = [
  "border-interstate text-interstate",
  "border-warning text-warning",
  "border-unknown text-unknown",
  "border-guide text-guide",
];

export function ConflictStrip({
  submissionId,
  field,
  active,
  onPick,
}: {
  submissionId: string;
  field: Schemas["FieldDetail"];
  active: number | undefined;
  onPick: (candidate: number) => void;
}) {
  const name = useSourceName(submissionId);
  return (
    <div className="mb-4 rounded-[var(--radius-panel)] border border-warning/30 bg-warning-soft/60 p-3">
      <p className="text-sm font-semibold text-warning">The documents disagree</p>
      {field.conflict?.hint ? (
        <p className="mt-0.5 text-sm text-ink">{field.conflict.hint}</p>
      ) : null}
      <div className="mt-2 flex flex-wrap gap-2">
        {field.candidates.map((c, i) => (
          <button
            key={i}
            type="button"
            onClick={() => onPick(i)}
            className={clsx(
              "rounded-[var(--radius-cell)] border-2 bg-surface px-2.5 py-1 text-left",
              CANDIDATE_TONES[i % CANDIDATE_TONES.length],
              active === i ? "opacity-100" : "opacity-60 hover:opacity-100",
            )}
          >
            <span className="block font-semibold text-ink">{c.display}</span>
            <span className="block text-xs">
              {c.sources.map((s) => name(s.title, s.document_id)).join("; ")}
            </span>
          </button>
        ))}
      </div>
      {field.conflict?.blocking_rules.length ? (
        <p className="mt-2 text-xs text-ink">
          Changes the outcome of {field.conflict.blocking_rules.join(", ")}
        </p>
      ) : null}
    </div>
  );
}
