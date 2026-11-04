import type { Schemas } from "@/lib/api";

/** One source shown in the lens: a fact on a page, or a value the system worked out. */
export type Item = {
  fact: Schemas["EvidenceFact"] | null;
  source: { title: string; doc_type?: string; note?: string | null };
  candidate: { display: string | null; superseded: string | null; index: number } | null;
  /** The value was worked out by code from this fact, not read as printed */
  derived?: boolean;
};

/** Every source behind a field, candidate by candidate. */
export function fieldItems(field: Schemas["FieldDetail"] | undefined): Item[] {
  const attribute = field?.attribute ?? "";
  return (field?.candidates ?? []).flatMap((c, index): Item[] => {
    // A worked-out value can cite one fact from several documents (the contact name): once
    const seen = new Set<string>();
    return c.sources.flatMap((s): Item[] => {
      const candidate = { display: c.display, superseded: c.superseded, index };
      if (!s.facts.length) return [{ fact: null, source: s, candidate }];
      // A row's facts are linked together; show the ones about this field.
      const own = s.facts.filter((f) => matchesAttribute(f.path, attribute));
      // No fact states the value itself: code worked it out from these (a count, a match)
      const derived = s.doc_type === "derived" || (!own.length && Boolean(s.note));
      return (own.length ? own : s.facts)
        .filter((f) => !seen.has(f.id) && Boolean(seen.add(f.id)))
        .map((f) => ({
          fact: f,
          // Worked out from several documents: each fact is titled by the one it is on
          source: derived ? { ...s, title: f.document_title || s.title } : s,
          candidate,
          derived,
        }));
    });
  });
}

export function factItems(facts: Schemas["EvidenceFact"][] | undefined): Item[] {
  return (facts ?? []).map((f) => ({
    fact: f,
    source: { title: f.document_title, doc_type: f.doc_type },
    candidate: null,
  }));
}

const ALIASES: Record<string, string[]> = {
  date_of_birth: ["dob", "birth"],
  license_number: ["license_no", "cdl_number", "license"],
  name: ["full_name", "driver_name", "first_name", "last_name"],
};

export function matchesAttribute(path: string, attribute: string): boolean {
  const last =
    path
      .replace(/\[\d+\]/g, "")
      .split(".")
      .pop()
      ?.toLowerCase() ?? "";
  if (!attribute) return true;
  return (
    last === attribute ||
    last.endsWith(attribute) ||
    (ALIASES[attribute] ?? []).some((a) => last.includes(a))
  );
}

export function shortTitle(title: string): string {
  const head = title.split(",")[0];
  return head.length > 34 ? `${head.slice(0, 32)}…` : head;
}
