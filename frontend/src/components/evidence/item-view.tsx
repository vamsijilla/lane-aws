"use client";

import clsx from "clsx";
import { plainCells, useFileName, useSourceName } from "@/lib/source-names";
import { Eye, ShieldCheck, TriangleAlert, Wand2 } from "lucide-react";
import { useState } from "react";

import { PageImage, PhotoReveal } from "@/components/evidence/page-image";
import type { Item } from "@/components/evidence/lens-items";
import { PageView } from "@/components/evidence/page-view";
import { SheetView, TextView } from "@/components/evidence/sheet-view";
import { ConfidenceBand } from "@/components/ui/chips";
import { Button, Skeleton } from "@/components/ui/primitives";
import type { Schemas } from "@/lib/api";
import { modelName, titleCase } from "@/lib/format";
import { usePages, useReveal } from "@/lib/queries";
import { useSession } from "@/lib/session";

const GROUNDING: Record<string, string> = {
  exact: "Found word for word on the page",
  fuzzy: "Found on the page, allowing for small reading differences",
  ocr_exact: "Read from the image and confirmed by OCR",
  ocr_fuzzy: "Read from the image; OCR agrees allowing for look-alike characters",
  cell: "Read directly from the spreadsheet cell",
  image: "Read from the image; OCR could not confirm it",
  none: "Not located on the page",
  label: "Found beside its label",
  row: "Found in its record's row",
  column: "Found under its column",
  line: "Printed as its own line",
  statement: "Printed verbatim",
  page: "On the page, not beside its label",
  mismatch: "A different value is printed beside its label",
  ocr_label: "OCR found it beside its label",
  ocr_row: "OCR found it in its record's row",
  ocr_column: "OCR found it under its column",
  ocr_line: "OCR found it as its own line",
  ocr_statement: "OCR found it printed verbatim",
  ocr_page: "OCR found it on the page, not beside its label",
  ocr_mismatch: "OCR reads a different value beside its label",
};

const METHOD: Record<string, string> = {
  text: "Text layer",
  vision: "Vision reading",
  sheet: "Spreadsheet",
  ocr: "OCR",
};

export function ItemView({ submissionId, item }: { submissionId: string; item: Item }) {
  const pages = usePages(submissionId);
  const fact = item.fact;
  const name = useSourceName(submissionId);
  const title = name(
    item.source.title,
    fact?.document_id,
    fact?.cell_ref ? [`cell ${fact.cell_ref}`] : [],
  );
  const page = fact?.page_id ? pages.data?.find((p) => p.id === fact.page_id) : undefined;
  // A cell is named by its file ("Vehicle list, cell F2"), not the workbook's sheet name
  const file = useFileName(submissionId)(fact?.document_id) || title;

  return (
    <div className="space-y-4">
      <div className="flex items-baseline justify-between gap-3">
        <p className="flex min-w-0 items-center font-semibold">
          <span className="truncate">{title}</span>
        </p>
        {fact?.page_no ? (
          <span className="shrink-0 text-sm text-graphite">Page {fact.page_no}</span>
        ) : null}
      </div>

      {fact?.verification ? (
        <p
          className={clsx(
            "flex items-start gap-1.5 text-sm",
            fact.verified ? "text-guide" : "text-warning",
          )}
        >
          {fact.verified ? (
            <ShieldCheck size={15} className="mt-0.5 shrink-0" aria-hidden="true" />
          ) : (
            <TriangleAlert size={15} className="mt-0.5 shrink-0" aria-hidden="true" />
          )}
          <span>
            <span className="font-semibold">How this was verified:</span>{" "}
            {plainCells(fact.verification, file)}
          </span>
        </p>
      ) : null}
      {fact && item.derived ? (
        <div className="border-l-2 border-interstate pl-3 text-sm">
          <p className="font-semibold">Worked out by code from these facts</p>
          {item.source.note ? <p className="mt-0.5 text-graphite">{item.source.note}</p> : null}
        </div>
      ) : null}
      {!fact ? (
        <div className="rounded-[var(--radius-panel)] border border-dashed border-hairline p-4 text-sm">
          <p className="font-semibold">Worked out by the system</p>
          <p className="mt-1 text-graphite">{item.source.note ?? "Derived from other facts."}</p>
        </div>
      ) : page && fact.cell_ref && page.kind === "sheet" ? (
        <SheetView pageId={page.id} cellRef={fact.cell_ref} />
      ) : page && page.kind === "email_body" ? (
        <TextView pageId={page.id} quote={fact.quote} />
      ) : page && page.width && page.height ? (
        <PreparedPage page={page}>
          <PageView
            key={`${page.id}-${fact.id}`}
            pageId={page.id}
            width={page.width}
            height={page.height}
            boxes={fact.bbox ? [fact.bbox] : []}
            label={`${title}, page ${fact.page_no ?? ""}`}
            personal={page.personal_photo}
          />
        </PreparedPage>
      ) : pages.isPending ? (
        <Skeleton lines={6} />
      ) : null}

      {fact ? (
        <dl className="grid grid-cols-[8.5rem_1fr] gap-x-4 gap-y-2 text-sm">
          {item.candidate ? (
            <>
              <dt className="text-graphite">Value</dt>
              <dd className="font-semibold">
                {item.candidate.display}
                {item.candidate.superseded ? (
                  <span className="ml-2 text-xs font-normal text-graphite">
                    Superseded: {item.candidate.superseded}
                  </span>
                ) : null}
              </dd>
            </>
          ) : null}
          <dt className="text-graphite">As printed</dt>
          <dd>
            <q className="rounded-[4px] bg-surface-sunk px-1">{fact.quote ?? fact.value}</q>
          </dd>
          <dt className="text-graphite">Read by</dt>
          <dd>
            {METHOD[fact.method] ?? titleCase(fact.method)}
            {fact.cell_ref ? (
              <span className="ml-1 font-mono text-graphite">{fact.cell_ref}</span>
            ) : null}
          </dd>
          {fact.verification ? (
            <>
              <dt className="text-graphite">How verified</dt>
              <dd className={fact.verified ? "" : "text-warning"}>
                {plainCells(fact.verification, file)}
                {fact.reads.length > 1 ? (
                  <ul className="mt-1 space-y-0.5 text-graphite">
                    {fact.reads.map((r) => (
                      <li key={`${r.by}-${r.value}`}>
                        {r.by === "code" ? "Code read" : `${modelName(r.by)} read`}:{" "}
                        <span className="font-mono text-ink">{r.value ?? "nothing"}</span>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </dd>
            </>
          ) : null}
          <dt className="text-graphite">Confidence</dt>
          <dd className="space-y-1">
            <ConfidenceBand band={fact.confidence} label />
            <ul className="space-y-0.5 text-graphite">
              <li>{GROUNDING[fact.grounding] ?? titleCase(fact.grounding)}</li>
              {fact.confidence_reasons
                .filter((r) => r !== fact.verification)
                .map((r) => (
                  <li key={r}>{plainCells(r, file)}</li>
                ))}
            </ul>
          </dd>
          <dt className="text-graphite">Field</dt>
          <dd className="font-mono text-xs text-graphite">{fact.path}</dd>
        </dl>
      ) : null}
    </div>
  );
}

export function PlainPage({
  submissionId,
  pageId,
  label,
  boxes = [],
}: {
  submissionId: string;
  pageId: string;
  label: string;
  boxes?: number[][];
}) {
  const pages = usePages(submissionId);
  const page = pages.data?.find((p) => p.id === pageId);
  if (!page) return <Skeleton lines={6} />;
  if (page.kind === "email_body") return <TextView pageId={page.id} quote={null} />;
  if (page.kind === "sheet") {
    return (
      <p className="text-sm text-graphite">
        Worksheet {page.sheet_name}. Open a value from it to see the exact cell.
      </p>
    );
  }
  return page.width && page.height ? (
    <PreparedPage page={page}>
      <PageView
        pageId={page.id}
        width={page.width}
        height={page.height}
        boxes={boxes}
        label={label}
        personal={page.personal_photo}
      />
    </PreparedPage>
  ) : null;
}

export function RevealButton({ fieldId }: { fieldId: string }) {
  const session = useSession();
  const reveal = useReveal();
  if (!session.can("reveal_pii")) {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-graphite">
        <ShieldCheck size={13} aria-hidden="true" /> Personal data hidden for this role
      </span>
    );
  }
  if (reveal.data) {
    return (
      <span className="inline-flex items-center gap-2 text-sm">
        <span className="font-mono font-semibold">
          {reveal.data.value_display ?? reveal.data.candidates.join(" / ")}
        </span>
        <span className="text-xs text-graphite">Reveal recorded in the audit trail</span>
      </span>
    );
  }
  return (
    <Button
      size="sm"
      variant="quiet"
      icon={Eye}
      busy={reveal.isPending}
      onClick={() => reveal.mutate(fieldId)}
    >
      Reveal
    </Button>
  );
}

/**
 * A page that was prepared before it was read (turned, straightened, flattened) says so, and can
 * show the page as it came. Boxes belong to the prepared page, so the original shows none.
 */
function PreparedPage({ page, children }: { page: Schemas["PageOut"]; children: React.ReactNode }) {
  const [original, setOriginal] = useState(false);
  const reveal = page.personal_photo ? <PhotoReveal pageId={page.id} /> : null;
  if (!page.prep.length)
    return reveal ? (
      <div className="space-y-2">
        {reveal}
        {children}
      </div>
    ) : (
      children
    );
  return (
    <div className="space-y-2">
      {reveal}
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-graphite">
        <Wand2 size={13} aria-hidden="true" />
        <span>Prepared for reading: {page.prep.join(". ")}.</span>
        {page.has_original ? (
          <button
            type="button"
            onClick={() => setOriginal((o) => !o)}
            aria-pressed={original}
            className="font-semibold text-interstate hover:underline"
          >
            {original ? "Show the prepared page" : "Show the original"}
          </button>
        ) : null}
      </p>
      {original ? (
        <PageImage
          pageId={page.id}
          variant="original"
          personal={page.personal_photo}
          alt={`Page ${page.page_no} as it came, before preparation`}
          className="w-full rounded-[var(--radius-cell)] border border-hairline"
        />
      ) : (
        children
      )}
    </div>
  );
}
