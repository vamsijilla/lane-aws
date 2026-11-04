import { describe, expect, it } from "vitest";

import type { SubmissionSummary } from "@/lib/api";

import { age, arrange, counts, duration, viewOf } from "./queue";

function sub(over: Partial<SubmissionSummary>): SubmissionSummary {
  return {
    id: "s",
    subject: "New business submission",
    sender_email: "agent@agency.example",
    sender_name: null,
    received_at: "2026-11-01T10:00:00Z",
    status: "ready",
    files: 3,
    documents: 4,
    duplicates: 0,
    archives: 0,
    filename_mismatches: 0,
    superseded: 0,
    needs_review: 0,
    different_formats: [],
    recommendation: "ready",
    recommendation_headline: "Ready for Small Fleet underwriter review",
    potential_decline: false,
    latest_run: null,
    ...over,
  } as SubmissionSummary;
}

const items = [
  sub({
    id: "a",
    received_at: "2026-11-01T08:00:00Z",
    recommendation: "refer",
    potential_decline: true,
    subject: "Granite Ridge Freight",
  }),
  sub({
    id: "b",
    received_at: "2026-11-01T09:00:00Z",
    recommendation: "ready",
    subject: "North Star",
  }),
  sub({
    id: "c",
    received_at: "2026-11-01T11:00:00Z",
    status: "processing",
    recommendation: null,
    recommendation_headline: null,
  }),
  sub({
    id: "d",
    received_at: "2026-11-01T07:00:00Z",
    recommendation: "decline",
    subject: "Hazmat Haulers",
  }),
];

describe("inbox queue", () => {
  it("files each submission under the views an underwriter works from", () => {
    expect(counts(items)).toEqual({
      underwriter: 1,
      decline: 2,
      ready: 1,
      processing: 1,
      all: 4,
    });
    expect(
      arrange(items, { view: "processing", query: "", sort: "received" }).map((s) => s.id),
    ).toEqual(["c"]);
  });

  it("searches subject, sender and route, and sorts by received, age or route", () => {
    expect(
      arrange(items, { view: "all", query: "north", sort: "received" }).map((s) => s.id),
    ).toEqual(["b"]);
    expect(arrange(items, { view: "all", query: "", sort: "received" }).map((s) => s.id)).toEqual([
      "c",
      "b",
      "a",
      "d",
    ]);
    expect(arrange(items, { view: "all", query: "", sort: "age" }).map((s) => s.id)).toEqual([
      "d",
      "a",
      "b",
      "c",
    ]);
    expect(arrange(items, { view: "all", query: "", sort: "route" }).map((s) => s.id)).toEqual([
      "d",
      "a",
      "b",
      "c",
    ]);
  });

  it("reads an unknown view from a link as All", () => {
    expect(viewOf("decline")).toBe("decline");
    expect(viewOf("nonsense")).toBe("all");
    expect(viewOf(null)).toBe("all");
  });

  it("states age against the turnaround target", () => {
    const now = Date.parse("2026-11-01T12:00:00Z");
    expect(age("2026-11-01T11:30:00Z", now, 4)).toEqual({
      age: "30m",
      left: "Due in 3h 30m",
      state: "ok",
    });
    expect(age("2026-11-01T08:30:00Z", now, 4)).toEqual({
      age: "3h 30m",
      left: "Due in 30m",
      state: "soon",
    });
    expect(age("2026-11-01T06:00:00Z", now, 4)).toEqual({
      age: "6h",
      left: "Was due 2h ago",
      state: "overdue",
    });
    expect(duration(3 * 86_400_000)).toBe("3 days");
  });
});
