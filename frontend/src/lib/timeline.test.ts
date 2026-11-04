import { describe, expect, it } from "vitest";

import type { Schemas } from "@/lib/api";

import { describeGroup, foldRoutine, isKey } from "./timeline";

type Event = Schemas["AuditEventOut"];
let seq = 100;
const ev = (action: string, kind: Event["kind"] = "system"): Event => ({
  seq: seq--,
  at: "2026-10-31T12:00:00Z",
  actor: kind === "human" ? "user:underwriter" : "system:pipeline",
  action,
  kind,
  summary: action,
  subject_type: "x",
  subject_id: "x",
  payload: {},
  hash: "h",
});

describe("timeline folding", () => {
  it("keeps people, security and outcomes, and folds routine runs between them", () => {
    const events = [
      ev("conflict.resolved", "human"),
      ev("step.succeeded"),
      ev("document.classified"),
      ev("document.classified"),
      ev("submission.decided"),
      ev("step.succeeded"),
      ev("security.injection_detected", "security"),
    ];
    const items = foldRoutine(events);
    expect(items.map((i) => i.type)).toEqual(["event", "group", "event", "event", "event"]);
    const group = items[1];
    expect(group.type === "group" && group.events).toHaveLength(3);
    expect(group.type === "group" && group.summary).toBe("2 documents recognised, 1 step");
  });

  it("names the largest families and counts the rest", () => {
    const events = [
      ...Array.from({ length: 4 }, () => ev("document.extracted")),
      ev("attachment.accepted"),
      ev("step.succeeded"),
      ev("run.started"),
      ev("email.resent"),
    ];
    expect(describeGroup(events)).toBe("4 extractions, 1 file event, 1 step and 2 more");
  });

  it("treats a replay verdict and the decision as key events", () => {
    expect(isKey(ev("run.replayed"))).toBe(true);
    expect(isKey(ev("submission.decided"))).toBe(true);
    expect(isKey(ev("document.extracted", "model"))).toBe(false);
  });
});
