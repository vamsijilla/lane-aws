"use client";

import { Table2 } from "lucide-react";

import { PageImage } from "@/components/evidence/page-image";
import type { Schemas } from "@/lib/api";

type Fact = Schemas["EvidenceFact"];
type Page = Schemas["PageOut"];

const W = 280;
const H = 56;

/**
 * The exact place a value was read: a crop of the page around its box, or the worksheet cell.
 * Clicking it opens the evidence lens (the parent supplies `onOpen`).
 */
export function SourceCrop({
  fact,
  page,
  onOpen,
}: {
  fact: Fact | undefined;
  page: Page | undefined;
  onOpen: () => void;
}) {
  if (!fact) return null;
  const label = `Open where this was read${fact.page_no ? `, page ${fact.page_no}` : ""}`;
  if (fact.cell_ref) {
    // The cell and its value are for the lens and for screen readers; the list shows only the
    // document and page
    const cell = fact.cell_ref.replace(/^'[^']*'!/, "");
    return (
      <button
        type="button"
        aria-label={`${label}: cell ${cell}, ${fact.quote ?? fact.value ?? ""}`}
        title={`Cell ${cell}`}
        onClick={(e) => {
          e.stopPropagation();
          onOpen();
        }}
        className="mt-1.5 inline-flex items-center rounded-[4px] border border-hairline bg-surface-sunk p-1 text-graphite hover:border-interstate hover:text-interstate"
      >
        <Table2 size={14} aria-hidden="true" />
      </button>
    );
  }
  const box = fact.bbox;
  if (!box || !page?.width || !page.height || !page.has_image) return null;
  const [x0, y0, x1, y1] = box;
  const padX = 24;
  const padY = 10;
  const rw = x1 - x0 + 2 * padX;
  const rh = y1 - y0 + 2 * padY;
  const scale = Math.min(W / rw, H / rh);
  return (
    <button
      type="button"
      aria-label={label}
      onClick={(e) => {
        e.stopPropagation();
        onOpen();
      }}
      className="relative mt-1.5 block overflow-hidden rounded-[4px] border border-hairline bg-white hover:border-interstate"
      style={{ width: Math.min(W, rw * scale), height: Math.min(H, rh * scale) }}
    >
      <PageImage
        pageId={page.id}
        personal={page.personal_photo}
        alt=""
        className="absolute max-w-none"
        style={{
          width: page.width * scale,
          height: page.height * scale,
          left: -(x0 - padX) * scale,
          top: -(y0 - padY) * scale,
        }}
      />
    </button>
  );
}
