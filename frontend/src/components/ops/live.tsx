"use client";

import clsx from "clsx";
import { CircleCheck, CircleX, Cpu, Database, HardDrive, Mail, RefreshCw } from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import Link from "next/link";

import { Chip, type Tone } from "@/components/ui/chips";
import { Panel } from "@/components/ui/primitives";
import type { Schemas } from "@/lib/api";
import { formatDateTime, titleCase } from "@/lib/format";
import { duration } from "@/lib/queue";

type Queue = Schemas["OpsQueue"];

const SERVICE_ICON: Record<string, typeof Database> = {
  Database: Database,
  "File storage": HardDrive,
  "Email intake": Mail,
};

const WORKER: Record<string, { tone: Tone; label: string }> = {
  working: { tone: "interstate", label: "Working" },
  idle: { tone: "guide", label: "Idle" },
  quiet: { tone: "stop", label: "No heartbeat" },
};

const JOB: Record<string, { tone: Tone; label: string }> = {
  queued: { tone: "neutral", label: "Queued" },
  running: { tone: "interstate", label: "Running" },
  done: { tone: "guide", label: "Done" },
  failed: { tone: "stop", label: "Failed" },
};

const LEASE_S = 30;

/**
 * The reliability layer, live: what the system runs on, which workers are alive (heartbeat every
 * 5 s), the job queue with each running job's lease, and jobs another worker took over when the
 * one holding them stopped. Refreshes every 2 s.
 */
export function LiveOps({ q }: { q: Queue }) {
  const reduce = useReducedMotion();
  const quiet = q.workers.filter((w) => w.state === "quiet").length;
  const alive = q.workers.length - quiet;
  return (
    <div className="space-y-6">
      <div className="grid gap-3 md:grid-cols-4">
        {q.services.map((s) => {
          const Icon = SERVICE_ICON[s.name] ?? Cpu;
          return (
            <div
              key={s.name}
              className="flex items-center gap-3 rounded-[var(--radius-panel)] border border-hairline p-3"
            >
              <Icon size={18} className="text-graphite" aria-hidden="true" />
              <div className="min-w-0 flex-1">
                <p className="text-sm text-graphite">{s.name}</p>
                <p className="truncate font-semibold">{s.detail}</p>
              </div>
              {s.ok ? (
                <CircleCheck size={16} className="text-guide" aria-label="Healthy" />
              ) : (
                <CircleX size={16} className="text-stop" aria-label="Not reachable" />
              )}
            </div>
          );
        })}
        <div className="flex items-center gap-3 rounded-[var(--radius-panel)] border border-hairline p-3">
          <RefreshCw size={18} className="text-graphite" aria-hidden="true" />
          <div className="min-w-0 flex-1">
            <p className="text-sm text-graphite">Work runs</p>
            <p className="font-semibold">
              {q.run_mode === "queue" ? "On workers, by lease" : "Inside the API"}
            </p>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Tile
          label="Workers alive"
          value={String(alive)}
          note={quiet ? `${quiet} without a heartbeat` : "All heartbeats current"}
          tone={quiet ? "stop" : undefined}
        />
        <Tile
          label="Waiting"
          value={String(q.jobs_by_status.queued ?? 0)}
          note={
            q.oldest_queued_seconds !== null && q.oldest_queued_seconds !== undefined
              ? `oldest ${duration(q.oldest_queued_seconds * 1000)}`
              : "Nothing waiting"
          }
        />
        <Tile
          label="Running"
          value={String(q.jobs_by_status.running ?? 0)}
          note={`${q.jobs_by_status.done ?? 0} done, ${q.jobs_by_status.failed ?? 0} failed`}
        />
        <Tile
          label="Model spend today"
          value={`$${q.today_cost_usd.toFixed(2)}`}
          note={`${q.today_calls.toLocaleString("en-US")} calls, ${Math.round(q.today_cache_rate * 100)}% from cache`}
        />
      </div>

      <Panel
        title="Workers"
        description="Each worker reports every 5 seconds. One that stops reporting loses its job when the lease runs out, and another worker takes it over."
      >
        {q.workers.length ? (
          <ul className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            <AnimatePresence initial={false}>
              {q.workers.map((w) => {
                const s = WORKER[w.state] ?? WORKER.idle;
                return (
                  <motion.li
                    key={w.owner}
                    layout={!reduce}
                    initial={reduce ? false : { opacity: 0, scale: 0.98 }}
                    animate={{ opacity: 1, scale: 1 }}
                    exit={reduce ? undefined : { opacity: 0 }}
                    className={clsx(
                      "rounded-[var(--radius-panel)] border p-3",
                      w.state === "quiet" ? "border-stop/40 bg-stop-soft/40" : "border-hairline",
                    )}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate font-mono text-sm" title={w.owner}>
                        {w.owner}
                      </span>
                      <Chip tone={s.tone}>{s.label}</Chip>
                    </div>
                    <p className="mt-1 text-sm text-graphite">
                      Seen {w.seen_seconds_ago}s ago, {w.jobs_done}{" "}
                      {w.jobs_done === 1 ? "job" : "jobs"} done, up since{" "}
                      {formatDateTime(w.started_at)}
                    </p>
                  </motion.li>
                );
              })}
            </AnimatePresence>
          </ul>
        ) : (
          <p className="text-graphite">
            {q.run_mode === "queue"
              ? "No worker is running. Start one with make worker; queued work waits for it."
              : "The API runs work itself (inline mode), so there are no workers."}
          </p>
        )}
      </Panel>

      <Panel title="Jobs" description="Newest first. A running job shows its lease." flush>
        {q.jobs.length ? (
          <table className="w-full text-base">
            <thead>
              <tr className="border-b border-hairline text-left text-sm text-graphite">
                <th className="px-4 py-2 font-normal">Submission</th>
                <th className="px-4 py-2 font-normal">Work</th>
                <th className="px-4 py-2 font-normal">Status</th>
                <th className="px-4 py-2 font-normal">Worker and lease</th>
                <th className="px-4 py-2 text-right font-normal">Queued</th>
              </tr>
            </thead>
            <tbody>
              {q.jobs.map((j) => {
                const s = JOB[j.status] ?? JOB.queued;
                return (
                  <tr key={j.id} className="border-b border-hairline last:border-0">
                    <td className="max-w-0 px-4 py-2">
                      <Link
                        href={`/submissions/${j.submission_id}/audit`}
                        className="block truncate font-semibold hover:text-interstate"
                        title={j.subject}
                      >
                        {j.subject}
                      </Link>
                    </td>
                    <td className="px-4 py-2 text-sm">
                      {titleCase(j.kind)}
                      <span className="text-graphite">, {j.trigger}</span>
                    </td>
                    <td className="px-4 py-2">
                      <span className="flex flex-wrap items-center gap-1.5">
                        <Chip tone={s.tone}>{s.label}</Chip>
                        {j.taken_over ? (
                          <Chip tone="warning">Taken over, attempt {j.attempts}</Chip>
                        ) : null}
                      </span>
                      {j.error ? (
                        <span className="mt-0.5 block truncate text-xs text-stop" title={j.error}>
                          {j.error}
                        </span>
                      ) : null}
                    </td>
                    <td className="px-4 py-2 text-sm">
                      {j.lease_owner ? (
                        <span className="font-mono">{j.lease_owner}</span>
                      ) : (
                        <span className="text-graphite">None</span>
                      )}
                      {j.lease_seconds_left !== null && j.lease_seconds_left !== undefined ? (
                        <Lease left={j.lease_seconds_left} />
                      ) : null}
                    </td>
                    <td className="px-4 py-2 text-right text-sm text-graphite tabular-nums">
                      {formatDateTime(j.created_at)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        ) : (
          <p className="px-4 py-4 text-graphite">
            No jobs yet. In queue mode, each email and reprocess becomes a job here.
          </p>
        )}
      </Panel>
    </div>
  );
}

/** Seconds left on a job's lease, as a bar that empties; expired means another worker may claim it. */
function Lease({ left }: { left: number }) {
  const expired = left <= 0;
  const pct = Math.max(0, Math.min(100, (left / LEASE_S) * 100));
  return (
    <span className="mt-1 flex items-center gap-2">
      <span
        className="h-1.5 w-24 overflow-hidden rounded-full bg-surface-sunk"
        role="meter"
        aria-label="Lease remaining"
        aria-valuemin={0}
        aria-valuemax={LEASE_S}
        aria-valuenow={Math.max(left, 0)}
      >
        <span
          className={clsx("block h-full", expired ? "bg-stop" : "bg-interstate")}
          style={{ width: `${pct}%` }}
        />
      </span>
      <span className={clsx("text-xs tabular-nums", expired ? "text-stop" : "text-graphite")}>
        {expired ? "expired, up for takeover" : `${left}s`}
      </span>
    </span>
  );
}

function Tile({
  label,
  value,
  note,
  tone,
}: {
  label: string;
  value: string;
  note?: string;
  tone?: "stop";
}) {
  return (
    <div
      className={clsx(
        "rounded-[var(--radius-panel)] border p-3",
        tone === "stop" ? "border-stop/40" : "border-hairline",
      )}
    >
      <p className="text-sm text-graphite">{label}</p>
      <p className="font-display text-2xl font-bold tabular-nums">{value}</p>
      {note ? <p className="text-xs text-graphite">{note}</p> : null}
    </div>
  );
}
