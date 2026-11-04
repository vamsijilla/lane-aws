"use client";

import clsx from "clsx";

import { colName, parseRef, useLayout } from "@/components/evidence/layout";
import { Skeleton } from "@/components/ui/primitives";

/** A window of the worksheet around the source cell, the cell outlined, its reference shown. */
export function SheetView({ pageId, cellRef }: { pageId: string; cellRef: string }) {
  const layout = useLayout(pageId);
  const target = parseRef(cellRef);
  if (layout.isPending) return <Skeleton lines={6} />;
  if (!layout.data || !("cells" in layout.data) || !target) {
    return <p className="text-sm text-graphite">This sheet could not be shown.</p>;
  }
  const sheet = layout.data;
  const byRef = new Map(sheet.cells.map((c) => [c.ref, c]));
  const hidden = new Set(sheet.hidden_cols);
  const cols: number[] = [];
  for (let c = Math.max(1, target.col - 3); c <= Math.min(sheet.max_col, target.col + 3); c++) {
    if (!hidden.has(colName(c)) || c === target.col) cols.push(c);
  }
  const rows: number[] = [];
  for (let r = Math.max(1, target.row - 5); r <= Math.min(sheet.max_row, target.row + 5); r++)
    rows.push(r);

  return (
    <div className="space-y-2">
      <div className="overflow-x-auto rounded-[var(--radius-cell)] border border-hairline">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="bg-surface-sunk text-xs text-graphite">
              <th className="w-9 border-r border-b border-hairline px-1 py-1 font-normal" />
              {cols.map((c) => (
                <th
                  key={c}
                  className={clsx(
                    "border-r border-b border-hairline px-2 py-1 font-mono font-normal",
                    c === target.col && "bg-interstate-soft text-interstate",
                  )}
                >
                  {colName(c)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r}>
                <th
                  className={clsx(
                    "border-r border-b border-hairline bg-surface-sunk px-1 py-1 text-right font-mono text-xs font-normal text-graphite",
                    r === target.row && "bg-interstate-soft text-interstate",
                  )}
                >
                  {r}
                </th>
                {cols.map((c) => {
                  const ref = `${colName(c)}${r}`;
                  const cell = byRef.get(ref);
                  const hit = c === target.col && r === target.row;
                  return (
                    <td
                      key={ref}
                      className={clsx(
                        "max-w-40 truncate border-r border-b border-hairline px-2 py-1",
                        hit && "lens-cell relative bg-interstate-soft font-semibold text-ink",
                      )}
                      title={cell ? String(cell.v) : undefined}
                    >
                      {cell ? display(cell.v, cell.fmt) : ""}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-sm text-graphite">
        Sheet <span className="font-semibold text-ink">{sheet.sheet}</span>, cell{" "}
        <span className="font-mono font-semibold text-ink">
          {colName(target.col)}
          {target.row}
        </span>
      </p>
    </div>
  );
}

function display(v: unknown, fmt: string | null): string {
  if (typeof v === "number" && fmt?.includes("%")) return `${(v * 100).toFixed(0)}%`;
  if (typeof v === "boolean") return v ? "TRUE" : "FALSE";
  return v === null || v === undefined ? "" : String(v);
}

/** The email body with the quoted span highlighted. */
export function TextView({ pageId, quote }: { pageId: string; quote: string | null }) {
  const layout = useLayout(pageId);
  if (layout.isPending) return <Skeleton lines={5} />;
  const text = layout.data && "text" in layout.data ? layout.data.text : "";
  const at = quote ? text.toLowerCase().indexOf(quote.toLowerCase().slice(0, 80)) : -1;
  return (
    <pre
      tabIndex={0}
      className="max-h-[420px] overflow-auto rounded-[var(--radius-cell)] border border-hairline bg-surface-sunk p-4 font-sans text-sm leading-relaxed whitespace-pre-wrap"
    >
      {at < 0 || !quote ? (
        text
      ) : (
        <>
          {text.slice(0, at)}
          <mark className="lens-mark rounded-[4px] bg-interstate-soft px-0.5 text-ink outline outline-1 outline-interstate">
            {text.slice(at, at + quote.length)}
          </mark>
          {text.slice(at + quote.length)}
        </>
      )}
    </pre>
  );
}
