import clsx from "clsx";
import { CircleAlert, CircleCheck, CircleX, LoaderCircle, type LucideIcon } from "lucide-react";

import type { SubmissionSummary } from "@/lib/api";

type Tone = "info" | "ok" | "warn" | "stop";

// Text in the status colour; the icon is the only mark, no filled pill
const TONE: Record<Tone, string> = {
  info: "text-interstate",
  ok: "text-guide",
  warn: "text-warning",
  stop: "text-stop",
};

const STEP_LABEL: Record<string, string> = {
  triage: "Reading pages",
  classify: "Identifying documents",
};

export function describeStatus(s: SubmissionSummary): {
  tone: Tone;
  label: string;
  icon: LucideIcon;
  spin?: boolean;
} {
  if (s.status === "processing" || s.latest_run?.status === "running") {
    const step = s.latest_run?.current_step;
    return {
      tone: "info",
      label: (step && STEP_LABEL[step]) || "Processing",
      icon: LoaderCircle,
      spin: true,
    };
  }
  if (s.status === "failed") return { tone: "stop", label: "Failed", icon: CircleX };
  if (s.status === "received") return { tone: "info", label: "Queued", icon: LoaderCircle };
  if (s.latest_run?.status === "degraded" || s.needs_review > 0) {
    return { tone: "warn", label: "Needs attention", icon: CircleAlert };
  }
  // Once an underwriter decides, the status is that decision
  const decided = s.decision ? DECIDED[s.decision] : undefined;
  if (decided) return { ...decided, icon: CircleCheck };
  return { tone: "ok", label: "Ready", icon: CircleCheck };
}

const DECIDED: Record<string, { tone: Tone; label: string }> = {
  decline: { tone: "stop", label: "Declined" },
  refer_back: { tone: "warn", label: "Referred back to agent" },
  quote: { tone: "ok", label: "Proceeding to quote" },
};

export function SubmissionStatus({ submission }: { submission: SubmissionSummary }) {
  const { tone, label, icon: Icon, spin } = describeStatus(submission);
  return (
    <span className={clsx("inline-flex items-center gap-1.5 text-sm", TONE[tone])}>
      <Icon size={14} aria-hidden="true" className={clsx(spin && "motion-safe:animate-spin")} />
      {label}
    </span>
  );
}
