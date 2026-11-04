"use client";

import clsx from "clsx";
import { ChevronRight, Cog, Cpu, ShieldAlert, ShieldCheck, UserRound } from "lucide-react";
import { useState } from "react";

import { Runs } from "@/components/submission/audit-runs";
import { useSubmissionId } from "@/components/submission/shell";
import { ErrorNote, Panel, Skeleton } from "@/components/ui/primitives";
import type { Schemas } from "@/lib/api";
import { formatDateTime, personName } from "@/lib/format";
import { foldRoutine, isKey } from "@/lib/timeline";
import { useAudit, useRuns } from "@/lib/queries";

type Event = Schemas["AuditEventOut"];

const FILTERS = [
  ["key", "Key events"],
  ["all", "All"],
  ["human", "People"],
  ["security", "Security"],
  ["model", "Model"],
  ["system", "System"],
] as const;

export function Audit() {
  const id = useSubmissionId();
  const audit = useAudit(id);
  const runs = useRuns(id);
  const [filter, setFilter] = useState<(typeof FILTERS)[number][0]>("key");
  if (audit.isPending) return <Skeleton lines={12} />;
  if (audit.error)
    return <ErrorNote title="The audit trail could not be loaded">{audit.error.message}</ErrorNote>;
  const chain = audit.data.chain;
  return (
    <div className="space-y-6">
      <div
        className={clsx(
          // Verified is a quiet line; a broken chain is the one case that gets a red box
          "flex items-center gap-3",
          !chain.verified &&
            "rounded-[var(--radius-panel)] border border-stop/30 bg-stop-soft px-4 py-3",
        )}
      >
        {chain.verified ? (
          <ShieldCheck size={20} className="text-guide" aria-hidden="true" />
        ) : (
          <ShieldAlert size={20} className="text-stop" aria-hidden="true" />
        )}
        <div>
          <p className={clsx("font-semibold", chain.verified ? "text-guide" : "text-stop")}>
            {chain.verified
              ? `Audit chain verified, ${chain.events.toLocaleString("en-US")} events`
              : `Audit chain broken at event ${chain.first_bad_seq}`}
          </p>
          <p className="text-sm text-ink">
            {chain.verified
              ? "Every event's hash covers the one before it, so no past event can be edited or removed without this check failing."
              : chain.reason}
          </p>
        </div>
      </div>

      <Runs submissionId={id} runs={runs.data ?? []} />

      <Panel
        title="Timeline"
        description="Newest first. Who or what acted, and what happened."
        flush
      >
        <div
          className="flex flex-wrap gap-5 border-b border-hairline px-1"
          role="group"
          aria-label="Filter events"
        >
          {FILTERS.map(([key, label]) => {
            const n =
              key === "all"
                ? audit.data.events.length
                : key === "key"
                  ? audit.data.events.filter(isKey).length
                  : audit.data.events.filter((e) => e.kind === key).length;
            return (
              <button
                key={key}
                type="button"
                aria-pressed={filter === key}
                onClick={() => setFilter(key)}
                disabled={!n}
                className={clsx(
                  "border-b-2 py-1 text-sm tabular-nums disabled:opacity-40",
                  filter === key
                    ? "border-interstate text-ink"
                    : "border-transparent text-graphite hover:text-ink",
                )}
              >
                {label} {n}
              </button>
            );
          })}
        </div>
        <ol>
          {filter === "key"
            ? foldRoutine(audit.data.events).map((item) =>
                item.type === "event" ? (
                  <EventRow key={item.event.seq} e={item.event} />
                ) : (
                  <RoutineGroup key={item.id} events={item.events} summary={item.summary} />
                ),
              )
            : audit.data.events
                .filter((e) => filter === "all" || e.kind === filter)
                .map((e) => <EventRow key={e.seq} e={e} />)}
        </ol>
      </Panel>
    </div>
  );
}

/** A run of routine system events, folded to one line until opened. */
function RoutineGroup({ events, summary }: { events: Event[]; summary: string }) {
  const [open, setOpen] = useState(false);
  const first = events[0]; // newest first, like the timeline
  return (
    <li className="border-b border-hairline last:border-0">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="grid w-full grid-cols-[1.25rem_9.5rem_minmax(0,1fr)_4rem] items-baseline gap-3 px-4 py-2 text-left text-sm text-graphite hover:bg-surface-sunk/50"
      >
        <ChevronRight
          size={14}
          aria-hidden="true"
          className={clsx("translate-y-0.5 transition-transform", open && "rotate-90")}
        />
        <span className="tabular-nums">{formatDateTime(first.at)}</span>
        <span>
          {events.length} system events: {summary}
        </span>
        <span className="text-right font-mono text-xs">#{first.seq}</span>
      </button>
      {open ? (
        <ol className="border-t border-hairline/70 bg-surface-sunk/30">
          {events.map((e) => (
            <EventRow key={e.seq} e={e} />
          ))}
        </ol>
      ) : null}
    </li>
  );
}

const KIND_ICON = { human: UserRound, security: ShieldAlert, model: Cpu, system: Cog };

function EventRow({ e }: { e: Event }) {
  const Icon = KIND_ICON[e.kind];
  return (
    <li
      className={clsx(
        "grid grid-cols-[1.25rem_9.5rem_minmax(0,1fr)_4rem] items-baseline gap-3 border-b border-hairline px-4 py-2 text-sm last:border-0",
        e.kind === "human" && "bg-interstate-soft/30",
      )}
    >
      <Icon
        size={14}
        aria-label={e.kind}
        className={clsx(
          "translate-y-0.5",
          e.kind === "human" && "text-interstate",
          e.kind === "security" && "text-warning",
          e.kind === "model" && "text-unknown",
          e.kind === "system" && "text-graphite",
        )}
      />
      <span className="tabular-nums text-graphite">{formatDateTime(e.at)}</span>
      <span className="min-w-0">
        <span className={clsx(e.kind === "human" && "font-semibold")}>{e.summary}</span>
        <span className="ml-2 text-xs text-graphite">{personName(e.actor)}</span>
      </span>
      <span
        className="text-right font-mono text-xs text-graphite"
        title={`${e.action}, hash ${e.hash}`}
      >
        #{e.seq}
      </span>
    </li>
  );
}
