"use client";

import Link from "next/link";

import { LiveOps } from "@/components/ops/live";
import { Chip } from "@/components/ui/chips";
import { ErrorNote, PageTitle, Panel, Skeleton } from "@/components/ui/primitives";
import { formatDateTime, titleCase } from "@/lib/format";
import { useOps, useOpsQueue } from "@/lib/queries";
import { useSession } from "@/lib/session";

export function Ops() {
  const session = useSession();
  const ops = useOps();
  const queue = useOpsQueue(session.can("ops"));
  return (
    <div className="mx-auto max-w-6xl px-8 py-6">
      <PageTitle
        title="Operations"
        description="What the system runs on, its workers and work queue, live; then intake health over every run."
      />
      {!session.can("ops") ? (
        <div className="border-t border-hairline pt-5">
          <p className="font-semibold">Operations is for the operations and administrator roles</p>
          <p className="mt-1 max-w-[70ch] text-graphite">
            It shows workers, the job queue, model spend and failures. Choose Operations in the role
            menu in the top bar to see it.
          </p>
        </div>
      ) : ops.isPending || queue.isPending ? (
        <Skeleton lines={10} />
      ) : ops.error || queue.error ? (
        <ErrorNote title="Operations health could not be loaded">
          {(ops.error ?? queue.error)?.message}
        </ErrorNote>
      ) : (
        <div className="space-y-6">
          {queue.data ? <LiveOps q={queue.data} /> : null}
          <h2 className="pt-2 font-display text-lg font-bold">Intake health, every run</h2>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
            <Tile label="Submissions" value={String(ops.data.submissions)} />
            {Object.entries(ops.data.runs_by_status).map(([k, v]) => (
              <Tile key={k} label={`${titleCase(k)} runs`} value={String(v)} />
            ))}
            <Tile
              label="Model calls"
              value={ops.data.models.calls.toLocaleString("en-US")}
              note={`${Math.round((ops.data.models.cache_hits / Math.max(ops.data.models.calls, 1)) * 100)}% from cache, ${ops.data.models.errors} errors`}
            />
            <Tile
              label="Cost per submission"
              value={`$${ops.data.cost_per_submission_usd.toFixed(2)}`}
              note={`$${ops.data.models.cost_usd.toFixed(2)} in total`}
            />
          </div>
          <Panel title="Step timings" flush>
            <table className="w-full text-base">
              <thead>
                <tr className="border-b border-hairline text-left text-sm text-graphite">
                  <th className="px-4 py-2 font-normal">Step</th>
                  <th className="px-4 py-2 text-right font-normal">Runs</th>
                  <th className="px-4 py-2 text-right font-normal">Median</th>
                  <th className="px-4 py-2 text-right font-normal">95th percentile</th>
                  <th className="px-4 py-2 text-right font-normal">Failed</th>
                </tr>
              </thead>
              <tbody>
                {ops.data.steps.map((s) => (
                  <tr key={s.name} className="border-b border-hairline last:border-0">
                    <td className="px-4 py-2 font-semibold">{titleCase(s.name)}</td>
                    <td className="px-4 py-2 text-right tabular-nums">{s.runs}</td>
                    <td className="px-4 py-2 text-right tabular-nums">
                      {(s.p50_ms / 1000).toFixed(1)} s
                    </td>
                    <td className="px-4 py-2 text-right tabular-nums">
                      {(s.p95_ms / 1000).toFixed(1)} s
                    </td>
                    <td className="px-4 py-2 text-right tabular-nums">
                      {s.failed ? <Chip tone="stop">{s.failed}</Chip> : "0"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Panel>
          <Panel title="Failed runs" description="Retry by reprocessing the submission." flush>
            {ops.data.failed_runs.length ? (
              <ul>
                {ops.data.failed_runs.map((f) => (
                  <li
                    key={f.run_id}
                    className="grid grid-cols-[minmax(0,1fr)_8rem_minmax(0,1.4fr)] gap-3 border-b border-hairline px-4 py-2 text-sm last:border-0"
                  >
                    <Link
                      href={`/submissions/${f.submission_id}/audit`}
                      className="truncate font-semibold hover:text-interstate"
                    >
                      {f.subject}
                    </Link>
                    <span className="text-graphite">{formatDateTime(f.finished_at)}</span>
                    <span className="truncate text-stop" title={f.error ?? undefined}>
                      {f.step ? `${titleCase(f.step)}: ` : ""}
                      {f.error}
                    </span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="px-4 py-4 text-guide">No failed runs.</p>
            )}
          </Panel>
        </div>
      )}
    </div>
  );
}

function Tile({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="rounded-[var(--radius-panel)] border border-hairline p-3">
      <p className="text-sm text-graphite">{label}</p>
      <p className="font-display text-2xl font-bold tabular-nums">{value}</p>
      {note ? <p className="text-xs text-graphite">{note}</p> : null}
    </div>
  );
}
