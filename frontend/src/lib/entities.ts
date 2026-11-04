import type { Schemas } from "@/lib/api";

export type Entity = Schemas["EntityOut"];
export type Field = Schemas["FieldOut"];

export function ofKind(entities: Entity[] | undefined, kind: string): Entity[] {
  return (entities ?? []).filter((e) => e.kind === kind);
}

export function one(entities: Entity[] | undefined, kind: string): Entity | undefined {
  return ofKind(entities, kind)[0];
}

export function field(e: Entity | undefined, attribute: string): Field | undefined {
  return e?.fields.find((f) => f.attribute === attribute);
}

export function display(e: Entity | undefined, attribute: string): string | null {
  const f = field(e, attribute);
  if (!f || f.state === "missing") return null;
  if (f.state === "conflict") return candidateDisplays(f).join(" or ");
  return f.value_display;
}

export function candidateDisplays(f: Field): string[] {
  return f.candidates.filter((c) => !c.superseded).map((c) => String(c.display ?? ""));
}

export function toNumber(v: unknown): number | null {
  if (typeof v === "number") return v;
  if (typeof v !== "string") return null;
  const n = Number(v.replace(/[$,\s]/g, ""));
  return Number.isFinite(n) ? n : null;
}

export function isoDate(v: unknown): Date | null {
  if (typeof v !== "string" || !v) return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(v);
  if (m) return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  const us = /^(\d{1,2})\/(\d{1,2})\/(\d{2,4})$/.exec(v.trim());
  if (us) {
    const y = Number(us[3]) < 100 ? 2000 + Number(us[3]) : Number(us[3]);
    return new Date(y, Number(us[1]) - 1, Number(us[2]));
  }
  return null;
}
