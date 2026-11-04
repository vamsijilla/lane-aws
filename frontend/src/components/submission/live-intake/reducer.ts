/*
 * Live intake state: a pure reduction of the pipeline's Server-Sent Events, so the animation
 * can be replayed and tested without a browser.
 */

export type Attachment = {
  id: string;
  filename: string;
  role: string;
  status: string;
  kind: string | null;
  duplicate_of_id: string | null;
  parent_id: string | null;
};

export type Doc = {
  id: string;
  type: string;
  title: string;
  mismatch: boolean;
  hint: string | null;
  superseded: boolean;
  known: boolean; // typed by a known layout, no model call
};

export type State = {
  connected: boolean;
  subject: string;
  from: string;
  attachments: Attachment[];
  pages: Record<string, { total: number; scans: number }>;
  docs: Record<string, Doc[]>;
  steps: Record<string, string>;
  stepOrder: string[];
  facts: number;
  conflicts: string[];
  missing: string[];
  rules: { id: string; outcome: string; potential: boolean }[];
  decision: { outcome: string; headline: string; potential: boolean } | null;
  finished: boolean;
  last: string;
};

export const INITIAL: State = {
  connected: false,
  subject: "",
  from: "",
  attachments: [],
  pages: {},
  docs: {},
  steps: {},
  stepOrder: [],
  facts: 0,
  conflicts: [],
  missing: [],
  rules: [],
  decision: null,
  finished: false,
  last: "Connecting",
};

export type Event = { type: string; data: Record<string, unknown> };

const str = (v: unknown) => (typeof v === "string" ? v : "");
const num = (v: unknown) => (typeof v === "number" ? v : 0);

export const STEP_LABEL: Record<string, string> = {
  triage: "Reading pages",
  classify: "Recognising documents",
  extract: "Extracting facts",
  resolve: "Reconciling",
  rules: "Applying the guideline",
  analyze: "Expert review",
  actions: "Preparing the agent request",
};

export function reduce(s: State, e: Event): State {
  const d = e.data;
  switch (e.type) {
    case "snapshot": {
      const email = (d.email ?? {}) as Record<string, unknown>;
      return {
        ...s,
        connected: true,
        subject: str(email.subject),
        from: str(email.from),
        attachments: (d.attachments as Attachment[]) ?? [],
        last: "Email received",
      };
    }
    case "run.started":
      return {
        ...s,
        stepOrder: (d.steps as string[]) ?? [],
        finished: false,
        last: "Processing started",
      };
    case "step.started":
      return {
        ...s,
        steps: { ...s.steps, [str(d.step)]: "running" },
        last: STEP_LABEL[str(d.step)] ?? str(d.step),
      };
    case "step.finished":
      return { ...s, steps: { ...s.steps, [str(d.step)]: str(d.status) } };
    case "page.ready": {
      const a = str(d.attachment_id);
      const cur = s.pages[a] ?? { total: 0, scans: 0 };
      const scan = d.kind === "scan" || d.kind === "photo";
      return {
        ...s,
        pages: { ...s.pages, [a]: { total: cur.total + 1, scans: cur.scans + (scan ? 1 : 0) } },
      };
    }
    case "document.found": {
      const a = str(d.attachment_id);
      const doc: Doc = {
        id: str(d.document_id),
        type: str(d.doc_type),
        title: str(d.title),
        mismatch: Boolean(d.filename_mismatch),
        hint: (d.filename_hint as string | null) ?? null,
        superseded: Boolean(d.superseded),
        known: Boolean(d.known_layout),
      };
      const list = s.docs[a] ?? [];
      if (list.some((x) => x.id === doc.id)) return s;
      return { ...s, docs: { ...s.docs, [a]: [...list, doc] }, last: doc.title };
    }
    case "document.extracted":
      return { ...s, facts: s.facts + num(d.facts) };
    case "conflict.found":
      return { ...s, conflicts: [...s.conflicts, str(d.title)] };
    case "missing.found":
      return { ...s, missing: [...s.missing, str(d.title)] };
    case "rule.evaluated":
      return {
        ...s,
        rules: [
          ...s.rules,
          { id: str(d.rule_id), outcome: str(d.outcome), potential: Boolean(d.potential_decline) },
        ],
      };
    case "decision.made":
      return {
        ...s,
        decision: {
          outcome: str(d.outcome),
          headline: str(d.headline),
          potential: Boolean(d.potential_decline),
        },
        last: str(d.headline),
      };
    case "run.finished":
      return { ...s, finished: true, last: "Done" };
    default:
      return s;
  }
}

export const EVENTS = [
  "snapshot",
  "run.started",
  "step.started",
  "step.finished",
  "page.ready",
  "document.found",
  "document.extracted",
  "conflict.found",
  "missing.found",
  "rule.evaluated",
  "decision.made",
  "analysis.done",
  "actions.ready",
  "run.finished",
];
