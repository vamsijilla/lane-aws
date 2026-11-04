/** Formatting helpers. Numbers use tabular figures in CSS; here they only get separators. */

const DATE = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric" });
const DATETIME = new Intl.DateTimeFormat("en-US", {
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
  hour12: true, // 12-hour everywhere, whatever the viewer's locale
});
const TIME = new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit", hour12: true });

/** "Feb 10, 2023" from an ISO date, a timestamp or a US date as printed ("02/10/2023"). */
export function formatDate(value: string | null | undefined): string {
  if (!value) return "";
  const us = /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/.exec(value.trim());
  const iso = us ? `${us[3]}-${us[1].padStart(2, "0")}-${us[2].padStart(2, "0")}` : value.trim();
  const d = new Date(/^\d{4}-\d{2}-\d{2}$/.test(iso) ? `${iso}T12:00:00` : iso);
  return Number.isNaN(d.getTime()) ? value : DATE.format(d);
}

/** "Q3 2025" from "2025-Q3". */
export function formatQuarter(value: string): string {
  const m = /^(\d{4})-Q([1-4])$/.exec(value.trim());
  return m ? `Q${m[2]} ${m[1]}` : value;
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : DATETIME.format(d);
}

/** "4:15 PM" */
export function formatTime(value: string | Date | null | undefined): string {
  if (!value) return "";
  const d = value instanceof Date ? value : new Date(value);
  return Number.isNaN(d.getTime()) ? String(value) : TIME.format(d);
}

export function formatNumber(value: number | string | null | undefined, digits = 0): string {
  if (value === null || value === undefined || value === "") return "";
  const n = typeof value === "number" ? value : Number(String(value).replace(/[$,\s]/g, ""));
  if (!Number.isFinite(n)) return String(value);
  return n.toLocaleString("en-US", {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  });
}

export function formatMoney(value: number | string | null | undefined): string {
  const s = formatNumber(value);
  return s ? `$${s}` : "";
}

export function relativeAge(value: string): string {
  const ms = Date.now() - new Date(value).getTime();
  const minutes = Math.round(ms / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  return `${Math.round(hours / 24)} d ago`;
}

export function plural(n: number, word: string, many?: string): string {
  return `${n.toLocaleString("en-US")} ${n === 1 ? word : (many ?? `${word}s`)}`;
}

export function titleCase(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1).replaceAll("_", " ");
}

/** 'claude-opus-5-5' -> 'Claude Opus 5.5' */
export function modelName(id: string): string {
  const m = /^claude-([a-z]+)-(\d+)-(\d+)$/.exec(id);
  return m ? `Claude ${m[1][0].toUpperCase()}${m[1].slice(1)} ${m[2]}.${m[3]}` : id;
}

/** Who did something, as a name: "user:underwriter" -> "Underwriter". */
export function personName(actor: string | null | undefined, fallback = "someone"): string {
  const name = (actor ?? "").replace(/^(user|system):/, "").trim();
  if (!name) return fallback;
  return name === name.toLowerCase() && !name.includes("@")
    ? name.charAt(0).toUpperCase() + name.slice(1)
    : name;
}
