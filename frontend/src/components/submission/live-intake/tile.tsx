"use client";

import clsx from "clsx";
import {
  Check,
  FileSpreadsheet,
  FileText,
  Image as ImageIcon,
  Loader2,
  Mail,
  ScanLine,
  ScanSearch,
} from "lucide-react";
import { AnimatePresence, motion } from "motion/react";

import type { Attachment, Doc } from "@/components/submission/live-intake/reducer";
import { titleCase } from "@/lib/format";

const DOC_LABEL = (t: string) => titleCase(t.replace("mvr", "MVR").replace("ifta", "IFTA"));

/** A document's label on its tile: a workbook's lookup sheet is ignored, not "Other". */
function docLabel(d: Doc): string {
  if (d.type === "other" && /lookup/i.test(d.title)) return "Ignored (lookup values)";
  return DOC_LABEL(d.type);
}

function FileIcon({ kind, filename }: { kind: string | null; filename: string }) {
  const props = {
    size: 16,
    className: "mt-0.5 shrink-0 text-graphite",
    "aria-hidden": true,
  } as const;
  if (filename === "Email body") return <Mail {...props} />;
  if (kind === "xlsx" || /\.xlsx?$/i.test(filename)) return <FileSpreadsheet {...props} />;
  if (kind?.startsWith("image")) return <ImageIcon {...props} />;
  return <FileText {...props} />;
}

export function Tile({
  a,
  index,
  pages,
  docs,
  reduced,
  running,
}: {
  a: Attachment;
  index: number;
  pages: { total: number; scans: number } | undefined;
  docs: Doc[];
  reduced: boolean;
  running: boolean;
}) {
  const mismatch = docs.some((d) => d.mismatch);
  const typed = docs.length === 1 ? docLabel(docs[0]) : null;
  // The email body's tile is already named "Email body": no second label saying so
  const label = typed && typed.toLowerCase() !== a.filename.toLowerCase() ? typed : null;
  const scanning = running && pages && pages.scans > 0;
  return (
    <motion.li
      layout={!reduced}
      initial={reduced ? false : { opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{
        duration: reduced ? 0 : 0.28,
        delay: reduced ? 0 : Math.min(index * 0.035, 0.5),
      }}
      className="relative overflow-hidden rounded-[var(--radius-panel)] border border-hairline px-3 py-2.5"
    >
      {scanning ? (
        <span
          className="pointer-events-none absolute inset-x-0 top-0 h-full bg-gradient-to-b from-transparent via-interstate/10 to-transparent motion-safe:animate-[scan_1.6s_ease-in-out_infinite]"
          aria-hidden="true"
        />
      ) : null}
      <div className="flex items-start gap-2">
        <FileIcon kind={a.kind} filename={a.filename} />
        <div className="min-w-0 flex-1">
          <p
            className={clsx(
              "truncate text-sm",
              mismatch ? "text-graphite line-through" : "font-semibold",
            )}
            title={a.filename}
          >
            {a.filename}
          </p>
          <AnimatePresence mode="popLayout">
            {label ? (
              <motion.p
                key={label}
                initial={reduced ? false : { opacity: 0, rotateX: -90 }}
                animate={{ opacity: 1, rotateX: 0 }}
                transition={{ duration: reduced ? 0 : 0.35 }}
                className={clsx("text-sm font-semibold", mismatch ? "text-warning" : "text-ink")}
              >
                {mismatch ? `Reads as ${label.toLowerCase()}` : label}
                {docs[0]?.known ? (
                  <ScanSearch
                    size={12}
                    className="ml-1 inline text-guide"
                    aria-label="Known layout, no model call"
                  />
                ) : null}
              </motion.p>
            ) : null}
          </AnimatePresence>
          {docs.length > 1 ? (
            <motion.ul layout={!reduced} className="mt-1 flex flex-wrap gap-1">
              {docs.map((d, i) => (
                <motion.li
                  key={d.id}
                  initial={reduced ? false : { opacity: 0, x: -6 }}
                  animate={{ opacity: 1, x: 0 }}
                  transition={{ duration: reduced ? 0 : 0.25, delay: reduced ? 0 : i * 0.06 }}
                  className={clsx(
                    "rounded-[4px] border px-1 text-[11px]",
                    d.superseded
                      ? "border-hairline text-graphite line-through"
                      : "border-interstate/30 text-interstate",
                  )}
                  title={d.title}
                >
                  {docLabel(d)}
                </motion.li>
              ))}
            </motion.ul>
          ) : null}
          <p className="mt-0.5 flex items-center gap-1 text-xs text-graphite">
            {pages ? (
              <>
                {pages.scans ? <ScanLine size={11} aria-hidden="true" /> : null}
                {pages.total} {pages.total === 1 ? "page" : "pages"}
                {pages.scans ? `, ${pages.scans} scanned` : ""}
              </>
            ) : (
              <>
                <Loader2 size={11} className="animate-spin" aria-hidden="true" /> Waiting
              </>
            )}
            {docs.length ? (
              <Check size={11} className="ml-auto text-guide" aria-label="Recognised" />
            ) : null}
          </p>
        </div>
      </div>
    </motion.li>
  );
}
