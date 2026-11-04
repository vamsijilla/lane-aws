"use client";

import Link from "next/link";

import { Chip } from "@/components/ui/chips";
import { ErrorNote, Panel, Skeleton } from "@/components/ui/primitives";
import { formatDateTime, personName } from "@/lib/format";
import { useKnownLayouts } from "@/lib/queries";

const KIND: Record<string, string> = {
  layout: "Landmarks",
  sheet: "Worksheet headers",
};

/**
 * Tier 2 of classification: layouts learned from documents a person confirmed, never shipped
 * or seeded from a packet. Each shows where it came from, how often it typed a page, and how the
 * AI audit of a sample of those pages went; a disagreement disables it.
 */
export function KnownLayouts() {
  const layouts = useKnownLayouts();
  if (layouts.isPending) return <Skeleton lines={6} />;
  if (layouts.error)
    return <ErrorNote title="Known layouts could not be loaded">{layouts.error.message}</ErrorNote>;
  const rows = [...layouts.data].sort(
    (a, b) => b.pages_matched - a.pages_matched || a.name.localeCompare(b.name),
  );
  const typed = rows.reduce((n, r) => n + r.pages_matched, 0);
  return (
    <Panel
      title="Known layouts"
      description={
        rows.length
          ? `Learned from documents people confirmed. ${typed.toLocaleString("en-US")} stored pages were typed this way, with no model call; the AI re-checks a sample, and a disagreement turns a layout off.`
          : "None yet. Lane learns a layout when a person confirms a document's type (Documents, Confirm type); until then every page is typed by tier 1 signals or the AI."
      }
      flush
    >
      {rows.length ? (
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-hairline text-left text-sm text-graphite">
              <th className="px-4 py-2 font-normal">Layout and where it came from</th>
              <th className="px-4 py-2 font-normal">Document type</th>
              <th className="px-4 py-2 font-normal">Recognised by</th>
              <th className="px-4 py-2 text-right font-normal">Pages typed</th>
              <th className="px-4 py-2 text-right font-normal">AI audit</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className="border-b border-hairline last:border-0">
                <td className="px-4 py-2">
                  <span className="font-semibold">{r.name}</span>
                  {r.status !== "active" ? (
                    <Chip tone="stop" className="ml-2">
                      Turned off
                    </Chip>
                  ) : null}
                  <span className="block text-xs text-graphite">
                    Page {r.source_page_no} of{" "}
                    <Link
                      href={`/submissions/${r.source_submission_id}/documents`}
                      className="text-interstate underline underline-offset-2"
                    >
                      the confirmed document
                    </Link>
                    , confirmed by {personName(r.confirmed_by)}, {formatDateTime(r.confirmed_at)}
                  </span>
                  {r.disabled_reason ? (
                    <span className="block text-xs text-stop">{r.disabled_reason}</span>
                  ) : null}
                </td>
                <td className="px-4 py-2">{r.doc_type_label}</td>
                <td className="px-4 py-2 text-graphite">
                  {KIND[r.kind] ?? r.kind}, {r.landmarks}
                </td>
                <td className="px-4 py-2 text-right tabular-nums">{r.pages_matched}</td>
                <td className="px-4 py-2 text-right tabular-nums">
                  {r.audited ? (
                    <span className={r.audit_agreed === r.audited ? "text-guide" : "text-stop"}>
                      {r.audit_agreed} of {r.audited} agree
                    </span>
                  ) : (
                    <span className="text-graphite">Not sampled yet</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </Panel>
  );
}
