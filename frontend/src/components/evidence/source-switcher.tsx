"use client";

import clsx from "clsx";
import { ChevronLeft, ChevronRight } from "lucide-react";

import { type Item, shortTitle } from "@/components/evidence/lens-items";
import { useSourceName } from "@/lib/source-names";

export function SourceSwitcher({
  submissionId,
  items,
  index,
  onChange,
}: {
  submissionId: string;
  items: Item[];
  index: number;
  onChange: (i: number) => void;
}) {
  const name = useSourceName(submissionId);
  // Each source by where it came from (never "(1 of 2)"), the cell's sheet telling tabs apart
  const nameOf = (it: Item) =>
    name(
      it.source.title,
      it.fact?.document_id,
      it.fact?.cell_ref ? [`cell ${it.fact.cell_ref}`] : [],
    );
  if (items.length < 2) return null;
  // A tab names its document and page; the full name (with its quarter or period) when the short
  // one would repeat, and what the value is when even that repeats
  const page = (it: Item) => (it.fact?.page_no ? `, p. ${it.fact.page_no}` : "");
  // Every tab of a document kind that appears more than once gets its full name
  const base = items.map((it) => shortTitle(nameOf(it)));
  const full = items.map((it, i) =>
    base.filter((x) => x === base[i]).length > 1 ? nameOf(it) + page(it) : base[i] + page(it),
  );
  const labels = items.map((it, i) =>
    full.filter((x) => x === full[i]).length > 1 && it.fact?.path
      ? `${full[i]}, ${fieldWords(it.fact.path)}`
      : full[i],
  );
  return (
    // Text tabs, one per document that states the value; the open one is underlined in the accent
    <div className="mb-4 flex items-center gap-2 border-b border-hairline">
      <button
        type="button"
        aria-label="Previous source"
        disabled={index === 0}
        onClick={() => onChange(index - 1)}
        className="rounded-[var(--radius-cell)] p-1 text-graphite hover:text-ink disabled:opacity-40"
      >
        <ChevronLeft size={14} />
      </button>
      <div className="flex min-w-0 flex-1 gap-4 overflow-x-auto" role="tablist">
        {items.map((it, i) => (
          <button
            key={i}
            type="button"
            role="tab"
            aria-selected={i === index}
            onClick={() => onChange(i)}
            className={clsx(
              "-mb-px shrink-0 border-b-2 py-2 text-sm whitespace-nowrap",
              i === index
                ? "border-interstate text-ink"
                : "border-transparent text-graphite hover:text-ink",
            )}
          >
            {labels[i]}
          </button>
        ))}
      </div>
      <button
        type="button"
        aria-label="Next source"
        disabled={index === items.length - 1}
        onClick={() => onChange(index + 1)}
        className="rounded-[var(--radius-cell)] p-1 text-graphite hover:text-ink disabled:opacity-40"
      >
        <ChevronRight size={14} />
      </button>
      <span className="text-xs whitespace-nowrap text-graphite">
        {index + 1} of {items.length}
      </span>
    </div>
  );
}

/** 'drivers[0].driver_name' -> 'driver name' */
function fieldWords(path: string): string {
  const last = path.split(".").pop() ?? path;
  return last.replace(/\[\d+\]/g, "").replaceAll("_", " ");
}
