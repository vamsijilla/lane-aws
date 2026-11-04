"use client";

import clsx from "clsx";
import {
  ChevronDown,
  ChevronRight,
  FileSpreadsheet,
  FileText,
  Image as ImageIcon,
  Mail,
  ScanLine,
  ScanSearch,
  TriangleAlert,
  Wand2,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { useLens } from "@/components/evidence/lens";
import { PageImage } from "@/components/evidence/page-image";
import { LabelDocument } from "@/components/submission/label-document";
import { useSubmissionId } from "@/components/submission/shell";
import { Chip, ConfidenceBand } from "@/components/ui/chips";
import { Button, ErrorNote, Panel, Skeleton } from "@/components/ui/primitives";
import type { Schemas } from "@/lib/api";
import { plural, titleCase } from "@/lib/format";
import {
  useDocumentFacts,
  useDocuments,
  useExtractions,
  useOverview,
  usePages,
} from "@/lib/queries";
import { useSourceName } from "@/lib/source-names";
import { useSession } from "@/lib/session";

type Doc = Schemas["DocumentOut"];
type Page = Schemas["PageOut"];
type Extraction = Schemas["ExtractionOut"];

const METHOD: Record<string, string> = {
  text: "Text layer",
  vision: "Vision and OCR",
  sheet: "Spreadsheet cells",
};
const QUALITY: Record<string, "guide" | "warning" | "stop" | "neutral"> = {
  clean: "guide",
  degraded: "warning",
  poor: "stop",
  unreadable: "stop",
};

export function Documents() {
  const id = useSubmissionId();
  const docs = useDocuments(id);
  const pages = usePages(id);
  const extractions = useExtractions(id);
  const sub = useOverview(id).data?.submission;
  if (docs.isPending || pages.isPending) return <Skeleton lines={12} />;
  if (docs.error)
    return <ErrorNote title="Documents could not be loaded">{docs.error.message}</ErrorNote>;

  const byFile = new Map<string, Doc[]>();
  for (const d of docs.data)
    byFile.set(d.attachment_id, [...(byFile.get(d.attachment_id) ?? []), d]);
  const pageById = new Map((pages.data ?? []).map((p) => [p.id, p]));
  const extraction = new Map((extractions.data ?? []).map((x) => [x.document_id, x]));
  const mismatches = docs.data.filter((d) => d.filename_mismatch).length;
  const superseded = docs.data.filter((d) => d.superseded_by_id).length;
  const files = [...byFile.values()].filter((l) => l[0].doc_type !== "email_body").length;
  const scans = (pages.data ?? []).filter((p) => p.kind === "scan" || p.kind === "photo").length;

  return (
    <div className="space-y-4">
      <p className="text-graphite">
        {plural(files, "file")} and the email body read into {plural(docs.data.length, "document")}{" "}
        by content, not by filename.{" "}
        {sub && sub.duplicates
          ? `${plural(sub.duplicates, "duplicate copy", "duplicate copies")}${sub.archives ? ` from ${plural(sub.archives, "zip file")}` : ""} merged with the files already received. `
          : ""}
        {mismatches ? `${plural(mismatches, "file")} named for something else. ` : ""}
        {sub?.different_formats?.length
          ? `${plural(sub.different_formats.length, "file")} in a different format (${sub.different_formats.join("; ")}). `
          : ""}
        {superseded ? `${superseded} superseded by a newer copy. ` : ""}
        {scans
          ? `${plural(scans, "scanned or photographed page")} read by vision and confirmed by OCR.`
          : ""}
      </p>
      <Panel flush>
        <div className="grid grid-cols-[minmax(0,1.3fr)_minmax(0,1.4fr)_9rem_8rem_7rem] gap-4 border-b border-hairline px-1 py-2.5 text-sm text-graphite">
          <span>File as sent</span>
          <span>Reads as</span>
          <span>Read by</span>
          <span>Quality</span>
          <span className="text-right">Facts</span>
        </div>
        <ul>
          {[...byFile.entries()].map(([att, list]) => (
            <FileRow key={att} docs={list} pageById={pageById} extraction={extraction} />
          ))}
        </ul>
      </Panel>
    </div>
  );
}

function FileIcon({ filename, pages }: { filename: string; pages: Page[] }) {
  const props = { size: 15, className: "shrink-0 text-graphite", "aria-hidden": true } as const;
  if (filename === "Email body") return <Mail {...props} />;
  if (pages.some((p) => p.kind === "sheet")) return <FileSpreadsheet {...props} />;
  if (pages.some((p) => p.kind === "photo")) return <ImageIcon {...props} />;
  if (pages.some((p) => p.kind === "scan")) return <ScanLine {...props} />;
  return <FileText {...props} />;
}

function FileRow({
  docs,
  pageById,
  extraction,
}: {
  docs: Doc[];
  pageById: Map<string, Page>;
  extraction: Map<string, Extraction>;
}) {
  // A link to #file-<attachment> (from an email's attachments) opens this file and scrolls to it
  const anchor = `file-${docs[0].attachment_id}`;
  const [open, setOpen] = useState(
    () => typeof window !== "undefined" && window.location.hash === `#${anchor}`,
  );
  const row = useRef<HTMLLIElement>(null);
  useEffect(() => {
    // On a client navigation the router writes the hash after this row mounts, so look again
    // once it has, and whenever the hash changes
    const go = () => {
      if (window.location.hash !== `#${anchor}`) return;
      setOpen(true);
      row.current?.scrollIntoView({ block: "center" });
    };
    const t = window.setTimeout(go, 0);
    window.addEventListener("hashchange", go);
    return () => {
      window.clearTimeout(t);
      window.removeEventListener("hashchange", go);
    };
  }, [anchor]);
  const filename = docs[0].filename;
  const pages = docs
    .flatMap((d) => d.page_ids.map((pid) => pageById.get(pid)))
    .filter((p): p is Page => Boolean(p));
  const mismatch = docs.some((d) => d.filename_mismatch);
  // Lookup sheets are skipped, not read; they have no method to show.
  const methods = [
    ...new Set(
      docs
        .map((d) => extraction.get(d.id))
        .filter((x) => x !== undefined && x.status !== "skipped" && x.method !== "none")
        .map((x) => x?.method ?? ""),
    ),
  ].filter(Boolean);
  const qualities = [...new Set(pages.map((p) => p.quality))];
  const mixed = qualities.length > 1 ? mixedReason(pages) : null;
  const facts = docs.reduce((a, d) => a + (extraction.get(d.id)?.facts ?? 0), 0);
  return (
    <li id={anchor} ref={row} className="scroll-mt-40 border-b border-hairline last:border-0">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="grid w-full grid-cols-[minmax(0,1.3fr)_minmax(0,1.4fr)_9rem_8rem_7rem] items-center gap-4 px-1 py-3 text-left hover:bg-surface/70"
      >
        <span className="flex min-w-0 items-center gap-2">
          {open ? (
            <ChevronDown size={14} aria-hidden="true" />
          ) : (
            <ChevronRight size={14} aria-hidden="true" />
          )}
          <FileIcon filename={filename} pages={pages} />
          <span
            className={clsx(
              "truncate",
              mismatch ? "text-graphite line-through decoration-warning" : "font-semibold",
            )}
            title={filename}
          >
            {filename}
          </span>
        </span>
        <span className="flex min-w-0 flex-wrap items-center gap-1">
          {docs.length === 1 ? (
            <span className={clsx("truncate font-semibold", mismatch && "text-warning")}>
              {docs[0].doc_type_label}
            </span>
          ) : (
            <span className="font-semibold">{plural(docs.length, "document")}</span>
          )}
          {mismatch ? <Chip tone="warning">Misnamed</Chip> : null}
          {!mismatch && docs.some((d) => d.format_note) ? (
            <Chip tone="neutral">{docs.find((d) => d.format_note)?.format_note}</Chip>
          ) : null}
          {docs.some((d) => d.superseded_by_id) ? (
            <Chip tone="neutral">Includes a superseded copy</Chip>
          ) : null}
          {docs.some((d) => d.needs_review) ? <Chip tone="unknown">Check</Chip> : null}
        </span>
        <span className="text-sm text-graphite">
          {methods.map((m) => METHOD[m] ?? titleCase(m)).join(", ")}
        </span>
        <span className="flex flex-wrap gap-1">
          {mixed ? (
            <span title={mixed}>
              <Chip tone="warning">{mixedShort(pages)}</Chip>
            </span>
          ) : (
            // Clean is the normal case: quiet text, so the one page that is not clean stands out
            qualities.map((q) =>
              q === "clean" ? (
                <span key={q} className="text-sm text-graphite">
                  Clean
                </span>
              ) : (
                <Chip key={q} tone={QUALITY[q] ?? "neutral"}>
                  {titleCase(q)}
                </Chip>
              ),
            )
          )}
        </span>
        <span className="text-right tabular-nums">
          {facts ? facts.toLocaleString("en-US") : ""}
        </span>
      </button>
      {open && mixed ? (
        <p className="bg-surface-sunk/40 px-4 pt-2 pl-12 text-sm text-graphite">
          Page quality is mixed: {mixed}
        </p>
      ) : null}
      {open ? (
        <ul className="space-y-3 bg-surface-sunk/40 px-4 pt-1 pb-4 pl-12">
          {docs.map((d) => (
            <DocDetail
              key={d.id}
              doc={d}
              pages={d.page_ids
                .map((pid) => pageById.get(pid))
                .filter((p): p is Page => Boolean(p))}
              extraction={extraction.get(d.id)}
            />
          ))}
        </ul>
      ) : null}
    </li>
  );
}

/** One chip for a file whose pages differ: "Mixed (2 photos degraded)". */
function mixedShort(pages: Page[]): string {
  const weak = pages.filter((p) => p.quality !== "clean");
  const kinds = new Set(weak.map((p) => p.kind));
  const noun =
    kinds.size === 1 && kinds.has("photo")
      ? "photo"
      : kinds.size === 1 && kinds.has("scan")
        ? "scan"
        : "page";
  const bands = [...new Set(weak.map((p) => p.quality))].join(" or ");
  return `Mixed (${plural(weak.length, noun)} ${bands})`;
}

/** "2 of 10 pages are degraded (pages 7 and 10): soft focus; small print may be misread." */
function mixedReason(pages: Page[]): string {
  const weak = pages.filter((p) => p.quality !== "clean");
  const nums = weak.map((p) => p.page_no);
  const list =
    nums.length > 1 ? `${nums.slice(0, -1).join(", ")} and ${nums[nums.length - 1]}` : `${nums[0]}`;
  const why = [...new Set(weak.flatMap((p) => p.warnings))].join("; ");
  const bands = [...new Set(weak.map((p) => p.quality))].join(" or ");
  return `${weak.length} of ${pages.length} pages are ${bands} (page${nums.length > 1 ? "s" : ""} ${list})${why ? `: ${why.charAt(0).toLowerCase()}${why.slice(1)}` : ""}.`;
}

/** What was read from this document: how many fields, how many verified, what is not on it. */
function Coverage({ c, x }: { c: NonNullable<Extraction["coverage"]>; x: Extraction }) {
  const cost =
    x.cost_usd === null || x.cost_usd === undefined
      ? ""
      : x.cost_usd === 0
        ? " Read from cache."
        : ` Read for $${x.cost_usd.toFixed(2)}${x.latency_ms ? ` in ${(x.latency_ms / 1000).toFixed(1)} s` : ""}.`;
  return (
    <div className="mt-2 text-sm text-graphite">
      <p>
        {plural(c.found, "field")} read, {c.verified} verified
        {c.missing.length ? `; not on the document: ${c.missing.map(fieldName).join(", ")}` : ""}.
        {cost}
      </p>
      {c.decision_unverified.length ? (
        <p className="text-warning">
          Not verified: {c.decision_unverified.map(fieldName).join(", ")}. Both reads are kept for
          review.
        </p>
      ) : null}
    </div>
  );
}

const ACRONYM: Record<string, string> = {
  fein: "FEIN",
  ssn: "SSN",
  mc: "MC",
  dot: "USDOT",
  vin: "VIN",
  cdl: "CDL",
  ifta: "IFTA",
  mvr: "MVR",
  um: "UM",
  dob: "date of birth",
};

/** A catalog path as people say it: 'dot_number' -> 'USDOT number', 'agency.name' -> 'agency
 * name', 'drivers[].license_number' -> 'drivers license number'. */
export function fieldName(path: string): string {
  return path
    .replace(/\[\]/g, "")
    .split(/[._]/)
    .filter(Boolean)
    .map((w) => ACRONYM[w.toLowerCase()] ?? w.toLowerCase())
    .join(" ");
}

/** Internal ranking scores stay out of the reasons a person reads. */
function withoutScores(reason: string): string {
  return reason.replace(/\s*\(score [\d.]+, next [\d.]+\)/, "");
}

function DocDetail({
  doc,
  pages,
  extraction,
}: {
  doc: Doc;
  pages: Page[];
  extraction: Extraction | undefined;
}) {
  const submissionId = useSubmissionId();
  const lens = useLens();
  const session = useSession();
  const name = useSourceName(submissionId);
  const [showFacts, setShowFacts] = useState(false);
  const facts = useDocumentFacts(showFacts ? doc.id : null);
  const checks = extraction?.checks ?? [];
  const failed = checks.filter((c) => !c.passed);
  return (
    <li className="rounded-[var(--radius-panel)] border border-hairline p-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className={clsx("font-semibold", doc.superseded_by_id && "text-graphite")}>
            {name(doc.title, doc.id)}
          </p>
          <p className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-graphite">
            <span>{doc.doc_type_label}</span>
            <Recognition doc={doc} />
            <span>
              {doc.page_from === doc.page_to
                ? `Page ${doc.page_from}`
                : `Pages ${doc.page_from} to ${doc.page_to}`}
            </span>
            <ConfidenceBand band={doc.confidence} label />
            {doc.filename_hint && doc.filename_mismatch ? (
              <span className="text-warning">
                Filename suggests {doc.filename_hint.replaceAll("_", " ")}
              </span>
            ) : null}
            {doc.format_note ? <span>{doc.format_note}</span> : null}
            {doc.superseded_by_id ? <span>Superseded by a newer copy</span> : null}
          </p>
          {pages.some((p) => p.prep.length) ? (
            <p className="mt-1 flex items-center gap-1.5 text-sm text-graphite">
              <Wand2 size={13} aria-hidden="true" />
              Prepared for reading: {[...new Set(pages.flatMap((p) => p.prep))].join(". ")}.
            </p>
          ) : null}
          {[...new Set(pages.flatMap((p) => p.warnings))].map((w) => (
            <p
              key={w}
              className="mt-1 flex items-center gap-1.5 text-sm font-semibold text-warning"
            >
              <TriangleAlert size={13} aria-hidden="true" />
              {w}
            </p>
          ))}
          {doc.confidence_reasons.length ? (
            <p className="mt-1 text-sm text-graphite">
              {doc.confidence_reasons.map(withoutScores).join(". ")}.
            </p>
          ) : null}
          <LabelDocument submissionId={submissionId} doc={doc} />
        </div>
        {extraction ? (
          <div className="flex items-center gap-2">
            <Chip
              tone={
                extraction.status === "ok"
                  ? "guide"
                  : extraction.status === "needs_escalation"
                    ? "warning"
                    : "stop"
              }
            >
              {extraction.status === "ok" ? "Read" : titleCase(extraction.status)}
            </Chip>
            {checks.length ? (
              <Chip tone={failed.length ? "warning" : "guide"}>
                {failed.length
                  ? `${failed.length} of ${checks.length} checks failed`
                  : checks.length === 1
                    ? "1 check passes"
                    : `${checks.length} checks pass`}
              </Chip>
            ) : null}
            {extraction.auto_escalated ? (
              <Chip tone="neutral">Re-read by the stronger model</Chip>
            ) : null}
            {session.can("view_content") && extraction.facts ? (
              <Button size="sm" variant="quiet" onClick={() => setShowFacts((s) => !s)}>
                {showFacts ? "Hide facts" : `${extraction.facts} facts`}
              </Button>
            ) : null}
          </div>
        ) : null}
      </div>
      {extraction?.coverage ? <Coverage c={extraction.coverage} x={extraction} /> : null}
      {session.can("view_content") ? (
        <ul className="mt-3 flex gap-2 overflow-x-auto pb-1">
          {pages.map((p) => (
            <li key={p.id} className="shrink-0">
              <button
                type="button"
                data-evidence=""
                onClick={() =>
                  lens.open({
                    kind: "page",
                    pageId: p.id,
                    label: `${doc.title}, page ${p.page_no}`,
                  })
                }
                className="group block overflow-hidden rounded-[var(--radius-cell)] border border-hairline bg-white hover:border-interstate"
                title={`Page ${p.page_no}: ${p.kind}, ${p.quality}`}
              >
                {p.has_image ? (
                  <PageImage
                    pageId={p.id}
                    variant="thumb"
                    personal={p.personal_photo}
                    alt={`Page ${p.page_no}`}
                    className="h-28 w-auto"
                    loading="lazy"
                  />
                ) : (
                  <span className="flex h-28 w-20 items-center justify-center text-xs text-graphite">
                    {p.kind === "sheet" ? p.sheet_name : "Text"}
                  </span>
                )}
              </button>
              <span className="mt-0.5 block text-center text-xs text-graphite">p. {p.page_no}</span>
            </li>
          ))}
        </ul>
      ) : null}
      {failed.length ? (
        <ul className="mt-2 space-y-0.5 text-sm text-warning">
          {failed.map((c, i) => (
            <li key={i}>{c.detail ?? c.name}</li>
          ))}
        </ul>
      ) : null}
      {showFacts ? (
        facts.isPending ? (
          <Skeleton lines={4} className="mt-3" />
        ) : (
          <table className="mt-3 w-full text-sm">
            <tbody>
              {(facts.data ?? []).slice(0, 200).map((f) => (
                <tr key={f.id} className="border-t border-hairline/70">
                  <td className="w-[40%] py-1 pr-3 font-mono text-xs text-graphite">{f.path}</td>
                  <td className="py-1 pr-3">
                    <button
                      type="button"
                      data-evidence=""
                      onClick={() =>
                        lens.open({
                          kind: "facts",
                          factIds: [f.id],
                          label: `${doc.title}: ${f.path}`,
                        })
                      }
                      className="text-left underline decoration-graphite/40 decoration-dotted underline-offset-[3px] hover:text-interstate"
                    >
                      {f.value}
                    </button>
                  </td>
                  <td className="py-1 text-right">
                    <ConfidenceBand band={f.confidence} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )
      ) : null}
    </li>
  );
}

const RECOGNISED_BY: Record<string, string> = {
  ai: "Read by AI",
  keywords: "Typed from keywords",
  person: "Labelled by a person",
};

/** How the type was decided. A known layout can show the landmarks it matched on the page. */
function Recognition({ doc }: { doc: Doc }) {
  const lens = useLens();
  const r = doc.recognition;
  if (r.method === "intake") return null;
  if (r.method !== "layout") {
    return <span className="text-graphite">{RECOGNISED_BY[r.method]}</span>;
  }
  const page = r.landmarks.find((l) => l.box)?.page_id;
  const boxes = r.landmarks.filter((l) => l.page_id === page && l.box).map((l) => l.box ?? []);
  return (
    <span className="inline-flex items-center gap-1.5">
      <Chip tone="guide" icon={ScanSearch} title={r.template_name ?? undefined}>
        Known layout
      </Chip>
      {page && boxes.length ? (
        <button
          type="button"
          data-evidence=""
          onClick={() =>
            lens.open({
              kind: "page",
              pageId: page,
              label: `${doc.title}: landmarks`,
              boxes,
              note: `Recognised as ${r.template_name ?? "a known layout"} by ${r.hits} of ${r.of} landmarks, with no model call.`,
            })
          }
          className="text-sm font-semibold text-interstate hover:underline"
        >
          Show landmarks
        </button>
      ) : null}
    </span>
  );
}
