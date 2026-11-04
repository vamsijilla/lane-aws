import { describe, expect, it } from "vitest";

import recorded from "../../../../e2e/fixtures/live-events.json";

import { INITIAL, reduce, type State } from "./reducer";

type Event = { type: string; data: Record<string, unknown> };
const replay = (events: Event[]): State => events.reduce(reduce, INITIAL);

describe("live intake reducer", () => {
  it("replays a recorded run to the decision", () => {
    const events = recorded as Event[];
    const s = replay(events);
    expect(s.connected).toBe(true);
    expect(s.finished).toBe(true);
    expect(s.decision?.outcome).toBe("refer");
    expect(s.decision?.potential).toBe(true);
    expect(s.rules).toHaveLength(events.filter((e) => e.type === "rule.evaluated").length);
    expect(s.conflicts).toHaveLength(events.filter((e) => e.type === "conflict.found").length);
    const docs = Object.values(s.docs).flat();
    expect(docs).toHaveLength(events.filter((e) => e.type === "document.found").length);
    expect(Object.values(s.steps).every((st) => st !== "running")).toBe(true);
  });

  it("counts pages and scans per attachment", () => {
    const s = replay([
      { type: "page.ready", data: { attachment_id: "a", kind: "text" } },
      { type: "page.ready", data: { attachment_id: "a", kind: "scan" } },
      { type: "page.ready", data: { attachment_id: "b", kind: "photo" } },
    ]);
    expect(s.pages).toEqual({ a: { total: 2, scans: 1 }, b: { total: 1, scans: 1 } });
  });

  it("ignores a document it has already seen, and unknown events", () => {
    const found = {
      type: "document.found",
      data: { attachment_id: "a", document_id: "d1", title: "T" },
    };
    const s = replay([found, found, { type: "something.else", data: {} }]);
    expect(s.docs.a).toHaveLength(1);
  });

  it("marks documents typed by a known layout", () => {
    const s = replay([
      {
        type: "document.found",
        data: { attachment_id: "a", document_id: "d1", title: "Loss run", known_layout: true },
      },
      { type: "document.found", data: { attachment_id: "b", document_id: "d2", title: "Photo" } },
    ]);
    expect(s.docs.a[0].known).toBe(true);
    expect(s.docs.b[0].known).toBe(false);
  });
});
