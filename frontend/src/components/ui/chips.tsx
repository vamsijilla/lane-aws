import clsx from "clsx";
import {
  CircleAlert,
  CircleCheck,
  CircleDashed,
  CircleHelp,
  CircleMinus,
  CircleSlash,
  OctagonX,
  TriangleAlert,
  type LucideIcon,
} from "lucide-react";

/*
 * Every state has an icon and a word, never color alone. Marks are coloured text with a small
 * icon, not filled pills, so a table of states stays quiet. Colors follow road signs:
 * green guides, amber warns, red stops, blue informs. UNKNOWN has its own hue so it never
 * reads as disabled. MISSING has no color at all: an empty dashed slot.
 */

export type Tone = "guide" | "warning" | "stop" | "unknown" | "note" | "neutral" | "interstate";

// A state chip without its own icon gets its tone's, so the word is never carried by color alone
const TONE_ICON: Partial<Record<Tone, LucideIcon>> = {
  guide: CircleCheck,
  warning: TriangleAlert,
  stop: CircleSlash,
  unknown: CircleHelp,
};

export const TONE_TEXT: Record<Tone, string> = {
  guide: "text-guide",
  warning: "text-warning",
  stop: "text-stop",
  unknown: "text-unknown",
  note: "text-note",
  interstate: "text-interstate",
  neutral: "text-graphite",
};

export function Chip({
  tone,
  icon: Icon,
  children,
  title,
  className,
}: {
  tone: Tone;
  icon?: LucideIcon;
  children: React.ReactNode;
  title?: string;
  className?: string;
}) {
  return (
    <span
      title={title}
      className={clsx(
        "inline-flex items-center gap-1 text-sm whitespace-nowrap",
        TONE_TEXT[tone],
        className,
      )}
    >
      {(() => {
        const Shown = Icon ?? TONE_ICON[tone];
        return Shown ? <Shown size={12} strokeWidth={2.4} aria-hidden="true" /> : null;
      })()}
      {children}
    </span>
  );
}

export const OUTCOMES: Record<string, { tone: Tone; icon: LucideIcon; label: string }> = {
  pass: { tone: "guide", icon: CircleCheck, label: "Pass" },
  flag: { tone: "note", icon: CircleAlert, label: "Note" },
  refer: { tone: "warning", icon: TriangleAlert, label: "Refer" },
  decline: { tone: "stop", icon: OctagonX, label: "Decline" },
  unknown: { tone: "unknown", icon: CircleHelp, label: "Can’t check yet" },
  "n/a": { tone: "note", icon: CircleMinus, label: "Not applicable" },
  ready: { tone: "guide", icon: CircleCheck, label: "Ready" },
};

export function OutcomeChip({ outcome, potential }: { outcome: string; potential?: boolean }) {
  if (potential) {
    return (
      <Chip tone="stop" icon={OctagonX} title="A decline if the disputed fact is confirmed">
        Potential decline
      </Chip>
    );
  }
  const o = OUTCOMES[outcome] ?? OUTCOMES.unknown;
  return (
    <Chip tone={o.tone} icon={o.icon}>
      {o.label}
    </Chip>
  );
}

const STATES: Record<string, { tone: Tone; icon: LucideIcon; label: string }> = {
  known: { tone: "guide", icon: CircleCheck, label: "Known" },
  conflict: { tone: "warning", icon: TriangleAlert, label: "Conflict" },
  invalid: { tone: "stop", icon: CircleSlash, label: "Invalid" },
};

/** A field's state. Missing is an empty slot: absence looks like absence. */
export function StateChip({ state, count }: { state: string; count?: number }) {
  if (state === "missing") return <MissingSlot />;
  const s = STATES[state] ?? STATES.known;
  const label = state === "conflict" && count ? `Conflict, ${count} values` : s.label;
  return (
    <Chip tone={s.tone} icon={s.icon} title={label}>
      {label}
    </Chip>
  );
}

export function MissingSlot({ label = "Not provided" }: { label?: string }) {
  return (
    <span className="inline-flex h-5 items-center gap-1 rounded-[var(--radius-cell)] border border-dashed border-graphite/50 px-1.5 text-xs whitespace-nowrap text-graphite">
      <CircleDashed size={12} aria-hidden="true" />
      {label}
    </span>
  );
}

const BANDS: Record<string, { bars: number; color: string; label: string }> = {
  high: { bars: 3, color: "bg-guide", label: "High confidence" },
  medium: { bars: 2, color: "bg-warning", label: "Medium confidence" },
  low: { bars: 1, color: "bg-stop", label: "Low confidence" },
};

export function ConfidenceBand({
  band,
  label = false,
  hinted = false,
}: {
  band: string;
  label?: boolean;
  // Inside a Hint that already names the band and its reasons: no second, native tooltip
  hinted?: boolean;
}) {
  const b = BANDS[band] ?? BANDS.low;
  return (
    <span
      className="inline-flex items-center gap-1.5 text-xs text-graphite"
      title={hinted ? undefined : b.label}
    >
      <span className="inline-flex items-end gap-[2px]" aria-hidden="true">
        {[1, 2, 3].map((i) => (
          <span
            key={i}
            className={clsx("w-[3px] rounded-[1px]", i <= b.bars ? b.color : "bg-hairline")}
            style={{ height: 3 + i * 3 }}
          />
        ))}
      </span>
      <span className={label ? undefined : "sr-only"}>{b.label}</span>
    </span>
  );
}

export function KindChip({ kind }: { kind: string }) {
  // A finding's kind is not a status: one quiet colour, told apart by its icon and word
  const tone: Tone = "neutral";
  return <Chip tone={tone}>{kind.charAt(0).toUpperCase() + kind.slice(1)}</Chip>;
}

export function SeverityMark({ severity }: { severity: string }) {
  const tone: Tone = severity === "high" ? "stop" : severity === "medium" ? "warning" : "neutral";
  const label = severity === "high" ? "High" : severity === "medium" ? "Medium" : "Low";
  return (
    <Chip tone={tone} title={`${label} severity`}>
      {label}
    </Chip>
  );
}
