"use client";

import { useQuery } from "@tanstack/react-query";

import { authedUrl, hasToken } from "@/lib/api";

/*
 * Page layouts are stored blobs (word boxes in page points, a worksheet cell grid, or the email
 * text), served as-is by GET /pages/{id}/layout, so their shape is declared here.
 */

/** [x0, y0, x1, y1, text, block, line, word, confidence?] in page points, origin top left. */
export type Word = [number, number, number, number, string, number, number, number, number?];

export type Cell = { ref: string; r: number; c: number; v: unknown; t: string; fmt: string | null };

export type PageLayout =
  | { unit: "pt"; width: number; height: number; words: Word[]; source?: string }
  | {
      unit?: undefined;
      sheet: string;
      cells: Cell[];
      merged: string[];
      col_widths: Record<string, number>;
      hidden_cols: string[];
      hidden_rows: number[];
      max_row: number;
      max_col: number;
    }
  | { unit: "text"; text: string };

export function useLayout(pageId: string | null) {
  return useQuery({
    queryKey: ["layout", pageId],
    enabled: hasToken() && Boolean(pageId),
    staleTime: Infinity,
    queryFn: async (): Promise<PageLayout> => {
      const res = await fetch(authedUrl(`/api/v1/pages/${pageId}/layout`));
      if (!res.ok) throw new Error(`Could not load the page (${res.status})`);
      return (await res.json()) as PageLayout;
    },
  });
}

/** A page image. A license photo comes back blurred unless `reveal` is set by an underwriter
 * (the API records each reveal in the audit trail). */
export function pageImageUrl(
  pageId: string,
  variant: "full" | "thumb" | "original" = "full",
  reveal = false,
): string {
  const query = new URLSearchParams();
  if (variant !== "full") query.set("variant", variant);
  if (reveal) query.set("reveal", "1");
  const qs = query.toString();
  return authedUrl(`/api/v1/pages/${pageId}/image${qs ? `?${qs}` : ""}`);
}

/** 'D5' -> {col: 4, row: 5}; tolerates a sheet prefix ("'Drivers'!D5"). */
export function parseRef(ref: string): { col: number; row: number; sheet: string | null } | null {
  const m = /^(?:'?([^'!]+)'?!)?\$?([A-Z]{1,3})\$?(\d+)$/.exec(ref.trim());
  if (!m) return null;
  let col = 0;
  for (const ch of m[2]) col = col * 26 + (ch.charCodeAt(0) - 64);
  return { col, row: Number(m[3]), sheet: m[1] ?? null };
}

export function colName(col: number): string {
  let s = "";
  for (let n = col; n > 0; n = Math.floor((n - 1) / 26))
    s = String.fromCharCode(65 + ((n - 1) % 26)) + s;
  return s;
}
