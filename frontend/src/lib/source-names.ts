"use client";

import { useDocuments, useOverview } from "@/lib/queries";

/** Where a source was read, in a few words: a PDF by its page ("page 1"); a sheet by its cell
 * ("cell L58"), its row ("row 4", cells along one record) or its column ("column E"). */
export function locationText(locations: string[]): string {
  const pages = [...new Set(locations.filter((l) => l.startsWith("page ")))];
  if (pages.length) return pages.join(", "); // a page is enough; a cited cell is from elsewhere
  // Cells on the sheet the source mostly cites (a derived value may cite one cell elsewhere)
  const cited = locations.filter((l) => l.startsWith("cell "));
  const sheets = cited.map(sheetOf);
  const main = [...sheets].sort(
    (x, y) => sheets.filter((v) => v === y).length - sheets.filter((v) => v === x).length,
  )[0];
  const cells = cited
    .filter((l) => sheetOf(l) === main)
    .map((l) => l.replace(/^cell (?:'[^']*'!)?/, ""));
  if (cells.length === 1) return `cell ${cells[0]}`;
  if (!cells.length) return "";
  const rows = new Set(cells.map((c) => c.replace(/^[A-Z]+/, "")));
  const cols = new Set(cells.map((c) => c.replace(/\d+$/, "")));
  return rows.size === 1
    ? `row ${[...rows][0]}`
    : cols.size === 1
      ? `column ${[...cols][0]}`
      : `cells ${cells.slice(0, 3).join(", ")}`;
}

/** The sheet a cell location names: "cell 'Drivers'!B4" -> "Drivers". */
export function sheetOf(location: string): string {
  return /^cell '([^']*)'!/.exec(location)?.[1] ?? "";
}

export type DocInfo = {
  id: string;
  doc_type: string;
  filename: string;
  attachment_id: string;
  title: string;
};

/**
 * A source named by where it came from, never by a split number ("(1 of 2)") or a file name in
 * capitals: a tab of the application workbook is "Application, drivers and vehicles tab"; another
 * file is its own name without the applicant ("Driver list"); a title drops the applicant's name.
 */
export function sourceName(
  title: string,
  doc: DocInfo | undefined,
  docs: DocInfo[],
  applicant: string,
  locations: string[],
): string {
  const strip = (text: string) =>
    applicant
      ? text.replace(new RegExp(`[,\\s-]*${escapeRe(applicant)}[,\\s-]*`, "i"), " ").trim()
      : text;
  const bare = title.replace(/\s*\(\d+ of \d+\)$/, "");
  if (bare === title || !doc) return strip(title).replace(/[,\s]+$/, "");
  const sameFile = docs.find(
    (d) => d.attachment_id === doc.attachment_id && d.doc_type === "application",
  );
  const sheet = locations.map(sheetOf).find(Boolean);
  if (sameFile)
    return sheet ? `Application, ${sheet.toLowerCase()} tab` : `Application, ${bare.toLowerCase()}`;
  return fileName(doc, applicant) || bare;
}

/** A document's file, as people call it: "VEHICLE LIST - ACME INC.xlsx" -> "Vehicle list". */
export function fileName(doc: DocInfo, applicant: string): string {
  const base = doc.filename.replace(/\.\w+$/, "");
  const bare = applicant
    ? base.replace(new RegExp(`[,\\s-]*${escapeRe(applicant)}[,\\s-]*`, "i"), " ")
    : base;
  const file = bare.trim().replace(/^[\s,-]+|[\s,-]+$/g, "");
  return file ? sentenceCase(file) : "";
}

function escapeRe(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function sentenceCase(text: string): string {
  const lower = text.toLowerCase();
  return lower.charAt(0).toUpperCase() + lower.slice(1);
}

/**
 * Source names by origin for one submission: pass a document's title and id (and where it was
 * read, for a tab of the application workbook). Used wherever a source is named.
 */
export function useSourceName(submissionId: string) {
  const documents = useDocuments(submissionId);
  const overview = useOverview(submissionId);
  const docs: DocInfo[] = documents.data ?? [];
  const byId = new Map(docs.map((d) => [d.id, d]));
  const applicant = overview.data?.applicant.legal_name ?? "";
  return (title: string, documentId?: string | null, locations: string[] = []) =>
    sourceName(title, documentId ? byId.get(documentId) : undefined, docs, applicant, locations);
}

/** A document's file name, for naming a cell ("Vehicle list, cell F2"). */
export function useFileName(submissionId: string) {
  const documents = useDocuments(submissionId);
  const overview = useOverview(submissionId);
  const byId = new Map((documents.data ?? []).map((d) => [d.id, d]));
  const applicant = overview.data?.applicant.legal_name ?? "";
  return (documentId?: string | null): string => {
    const doc = documentId ? byId.get(documentId) : undefined;
    return doc ? fileName(doc, applicant) : "";
  };
}

/** "Cell 'vehicle_export20261009'!F2: ..." -> "Vehicle list, cell F2: ...": a cell named by its
 * document, not the workbook's internal sheet name. */
export function plainCells(text: string, document: string): string {
  return text.replace(
    /\b[Cc]ell '[^']+'!([A-Z]{1,3}\d+)/g,
    (_m, ref: string) => `${document}, cell ${ref}`,
  );
}
