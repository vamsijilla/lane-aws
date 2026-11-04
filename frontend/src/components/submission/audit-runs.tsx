"use client";

import { useQueryClient } from "@tanstack/react-query";
import clsx from "clsx";
import { CircleCheck, CircleX, History, Repeat } from "lucide-react";
import { useEffect, useRef } from "react";

import { Chip } from "@/components/ui/chips";
import { Button, Panel } from "@/components/ui/primitives";
import type { Schemas } from "@/lib/api";
import { formatDateTime, titleCase } from "@/lib/format";
import { keys, useReplay } from "@/lib/queries";
import { useSession } from "@/lib/session";

type Run = Schemas["RunOut"];

const STEP_TONE: Record<string, "guide" | "warning" | "stop" | "interstate" | "neutral"> = {
  succeeded: "guide",
  degraded: "warning",
  failed: "stop",
  running: "interstate",
  skipped: "neutral",
};

const TRIGGER: Record<string, string> = {
  intake: "Intake",
  follow_up: "Amended by email from the agent",
  rerun: "Reprocessed",
  replay: "Replay",
  resolution: "Conflict resolved",
  reopen: "Conflict reopened",
  override: "Value corrected",
};

const short = (h: string | null | undefined) => (h ? h.slice(0, 12) : "none");

/**
 * Every run on this submission: its steps, the model calls it made (or answered from the cache),
 * and its fingerprints. Replay reruns every step from the stored files with model answers only
 * from the cache, against a copy, and proves the output is identical.
 */
export function Runs({ submissionId, runs }: { submissionId: string; runs: Run[] }) {
  const session = useSession();
  const replay = useReplay(submissionId);
  const replaying = runs.some((r) => r.trigger === "replay" && r.status === "running");
  const qc = useQueryClient();
  const was = useRef(replaying);
  useEffect(() => {
    // A replay just finished: its verdict is in the audit timeline too
    if (was.current && !replaying)
      void qc.invalidateQueries({ queryKey: keys.submission(submissionId) });
    was.current = replaying;
  }, [replaying, qc, submissionId]);
  return (
    <Panel
      title="Runs"
      description="Each time the pipeline ran, step by step, with what it cost and a hash of what it produced."
      flush
    >
      {session.can("process") ? (
        <div className="flex flex-wrap items-center gap-3 border-b border-hairline px-4 py-3">
          <Button
            icon={Repeat}
            busy={replay.isPending || replaying}
            disabled={replaying}
            onClick={() => replay.mutate()}
          >
            {replaying ? "Replaying" : "Replay this submission"}
          </Button>
          <p className="max-w-[70ch] text-sm text-graphite">
            Every step runs again from the stored files with the recorded versions. Model answers
            come only from the cache, so nothing is spent, and the stored results are not changed.
          </p>
        </div>
      ) : null}
      {replay.error ? (
        <p role="alert" className="border-b border-hairline px-4 py-2 text-sm text-stop">
          {replay.error.message}
        </p>
      ) : null}
      <ul>
        {runs.slice(0, 10).map((r) => (
          <RunRow key={r.id} r={r} />
        ))}
      </ul>
    </Panel>
  );
}

function RunRow({ r }: { r: Run }) {
  const calls = r.model_calls;
  return (
    <li className="border-b border-hairline px-4 py-3 last:border-0">
      <p className="flex flex-wrap items-center gap-2 text-sm">
        <Chip tone={STEP_TONE[r.status] ?? "interstate"}>{titleCase(r.status)}</Chip>
        <span className="font-semibold">{TRIGGER[r.trigger] ?? titleCase(r.trigger)}</span>
        <span className="text-graphite">{formatDateTime(r.started_at)}</span>
        {calls.calls ? (
          <span className="text-graphite">
            {calls.calls} model {calls.calls === 1 ? "call" : "calls"}, {calls.from_cache} from the
            cache, ${calls.cost_usd.toFixed(2)}
          </span>
        ) : null}
        {r.output_hash ? (
          <span className="ml-auto font-mono text-xs text-graphite" title={r.output_hash}>
            output {short(r.output_hash)}
          </span>
        ) : null}
      </p>
      {r.email ? <p className="mt-1 text-sm text-graphite">Email: {r.email}</p> : null}
      {r.replay ? <ReplayVerdict r={r} replay={r.replay} /> : null}
      {r.error ? <p className="mt-1.5 text-sm text-stop">{r.error}</p> : null}
      <ol className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
        {r.steps.map((s) => (
          <li key={s.name} className="text-xs" title={s.error ?? `${s.name} version ${s.version}`}>
            <span
              className={clsx(
                "mr-1.5 inline-block size-2 rounded-full",
                s.status === "succeeded" && "bg-guide",
                s.status === "degraded" && "bg-warning",
                s.status === "failed" && "bg-stop",
                s.status === "running" && "bg-interstate",
                s.status === "skipped" && "bg-hairline",
              )}
              aria-hidden="true"
            />
            <span className="font-semibold">{titleCase(s.name)}</span>
            <span className="ml-1 tabular-nums text-graphite">
              {s.duration_ms !== null ? `${(s.duration_ms / 1000).toFixed(1)} s` : ""}
            </span>
          </li>
        ))}
      </ol>
    </li>
  );
}

function ReplayVerdict({ r, replay }: { r: Run; replay: Schemas["ReplayOut"] }) {
  return (
    <div
      className={clsx(
        "mt-2 rounded-[var(--radius-cell)] border px-3 py-2 text-sm",
        replay.identical ? "border-guide/30 bg-guide-soft" : "border-warning/40 bg-warning-soft",
      )}
    >
      <p
        className={clsx(
          "flex items-center gap-1.5 font-semibold",
          replay.identical ? "text-guide" : "text-warning",
        )}
      >
        {replay.identical ? (
          <CircleCheck size={15} aria-hidden="true" />
        ) : (
          <CircleX size={15} aria-hidden="true" />
        )}
        {replay.identical ? "Output identical" : "Output differs"}
      </p>
      <p className="mt-0.5 font-mono text-xs text-ink">
        stored {short(replay.before_hash)}, replayed {short(r.output_hash)}, input{" "}
        {short(r.input_hash)}
        {r.ruleset ? `, rules ${r.ruleset}` : ""}
      </p>
      {replay.not_cached ? (
        <p className="mt-1">
          {replay.not_cached} model {replay.not_cached === 1 ? "answer was" : "answers were"} not in
          the cache. A replay never calls a model, so those steps could not be reproduced.
        </p>
      ) : null}
      {replay.differences.length ? (
        <p className="mt-1">Differs in: {replay.differences.join(", ")}.</p>
      ) : null}
      {replay.versions_changed.length ? (
        <p className="mt-1 flex items-start gap-1.5">
          <History size={14} className="mt-0.5 shrink-0" aria-hidden="true" />
          Versions changed since the stored run: {replay.versions_changed.join("; ")}. This is a
          what-if under the new versions.
        </p>
      ) : null}
    </div>
  );
}
