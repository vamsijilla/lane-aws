"use client";

import clsx from "clsx";
import { OctagonX } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";

import { STEP_LABEL } from "@/components/submission/live-intake/reducer";
import { Chip, OUTCOMES } from "@/components/ui/chips";

export function RuleMark({
  id,
  outcome,
  potential,
}: {
  id: string;
  outcome: string;
  potential: boolean;
}) {
  const o = OUTCOMES[outcome] ?? OUTCOMES.unknown;
  return (
    <Chip
      tone={potential ? "stop" : o.tone}
      icon={potential ? OctagonX : o.icon}
      title={`${id}: ${potential ? "potential decline" : o.label.toLowerCase()}`}
      className="font-mono"
    >
      {id}
    </Chip>
  );
}

export function Steps({ order, steps }: { order: string[]; steps: Record<string, string> }) {
  if (!order.length) return null;
  return (
    <ol className="flex shrink-0 items-center gap-1" aria-label="Pipeline steps">
      {order.map((name) => {
        const st = steps[name];
        return (
          <li key={name} title={`${STEP_LABEL[name] ?? name}: ${st ?? "waiting"}`}>
            <span
              className={clsx(
                "block h-1.5 w-8 rounded-full transition-colors duration-300",
                st === "running" && "animate-pulse bg-interstate",
                st === "succeeded" && "bg-guide",
                st === "degraded" && "bg-warning",
                st === "failed" && "bg-stop",
                !st && "bg-hairline",
              )}
            />
          </li>
        );
      })}
    </ol>
  );
}

export function Counter({
  label,
  value,
  reduced,
}: {
  label: string;
  value: number;
  reduced: boolean;
}) {
  return (
    <div className="rounded-[var(--radius-panel)] border border-hairline p-3">
      <p className="text-sm font-semibold text-graphite">{label}</p>
      <motion.p
        key={value}
        initial={reduced ? false : { opacity: 0.4, y: 4 }}
        animate={{ opacity: 1, y: 0 }}
        className="font-display text-2xl font-bold tabular-nums"
      >
        {value.toLocaleString("en-US")}
      </motion.p>
    </div>
  );
}

export function Tray({
  label,
  items,
  tone,
}: {
  label: string;
  items: string[];
  tone: "warning" | "dashed";
}) {
  return (
    <div className="rounded-[var(--radius-panel)] border border-hairline p-3">
      <p className="mb-2 flex justify-between text-sm font-semibold text-graphite">
        {label}
        <span className="tabular-nums text-ink">{items.length}</span>
      </p>
      <ul className="space-y-1">
        <AnimatePresence initial={false}>
          {items.slice(-6).map((t, i) => (
            <motion.li
              key={`${t}-${i}`}
              initial={{ opacity: 0, y: -8 }}
              animate={{ opacity: 1, y: 0 }}
              className={clsx(
                "truncate rounded-[4px] px-1.5 py-0.5 text-xs",
                tone === "warning"
                  ? "bg-warning-soft text-warning"
                  : "border border-dashed border-graphite/40 text-graphite",
              )}
            >
              {t}
            </motion.li>
          ))}
        </AnimatePresence>
      </ul>
    </div>
  );
}
