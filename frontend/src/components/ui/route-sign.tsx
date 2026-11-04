import clsx from "clsx";
import { ArrowRightLeft, CircleCheck, LoaderCircle, type LucideIcon, OctagonX } from "lucide-react";

type Tone = { soft: string; text: string; ring: string; dot: string; icon: LucideIcon };

const TONES: Record<string, Tone> = {
  ready: {
    soft: "bg-guide-soft",
    text: "text-guide",
    ring: "border-guide/25",
    dot: "bg-guide",
    icon: CircleCheck,
  },
  refer: {
    soft: "bg-warning-soft",
    text: "text-warning",
    ring: "border-warning/25",
    dot: "bg-warning",
    icon: ArrowRightLeft,
  },
  decline: {
    soft: "bg-stop-soft",
    text: "text-stop",
    ring: "border-stop/25",
    dot: "bg-stop",
    icon: OctagonX,
  },
  pending: {
    soft: "bg-interstate-soft",
    text: "text-interstate",
    ring: "border-interstate/25",
    dot: "bg-interstate",
    icon: LoaderCircle,
  },
};

function isDecline(line: React.ReactNode): boolean {
  return typeof line === "string" && /decline/i.test(line);
}

/**
 * The recommendation. Large: the verdict on the brief, the headline and one sentence in serif,
 * the reasons under it. Small: the headline as text with one coloured status mark, for headers, the inbox and the
 * top bar, so the decision reads the same everywhere without shouting.
 */
export function RouteSign({
  outcome,
  headline,
  lines,
  sentence,
  alarm,
  size = "lg",
  className,
}: {
  outcome: string;
  headline: string;
  lines?: React.ReactNode[];
  /** Large only: what stands in the way, in one sentence written by code from the counts */
  sentence?: string;
  /** Large only: a potential decline sits under the route; the status mark says so */
  alarm?: boolean;
  size?: "lg" | "sm" | "xs";
  className?: string;
}) {
  const tone = TONES[outcome] ?? TONES.pending;
  const real = (lines ?? []).filter(Boolean);

  if (size !== "lg") {
    // Small: plain text with one coloured status mark, not a row of pills
    return (
      <div
        role="status"
        aria-label={`Recommendation: ${headline}`}
        data-outcome={outcome}
        className={clsx(
          "inline-flex flex-wrap items-baseline gap-x-2 gap-y-0.5",
          size === "sm" ? "text-sm" : "text-xs",
          className,
        )}
      >
        <span className="inline-flex items-center gap-1.5 font-semibold whitespace-nowrap text-ink">
          <span className={clsx("size-2 shrink-0 rounded-full", tone.dot)} aria-hidden="true" />
          {headline}
        </span>
        {real.map((line, i) => (
          <span
            key={i}
            className={clsx(
              "whitespace-nowrap",
              isDecline(line) ? "font-semibold text-stop" : "text-graphite",
            )}
          >
            {line}
          </span>
        ))}
      </div>
    );
  }

  // Large: the verdict as an editorial sentence, no box and no icon tile. The headline is set in
  // ink, the sentence after it in graphite, and the reasons sit under one hairline.
  return (
    <div
      role="status"
      aria-label={`Recommendation: ${headline}`}
      data-outcome={outcome}
      className={className}
    >
      <p className={clsx("flex items-center gap-2 text-sm", alarm ? "text-stop" : tone.text)}>
        <span
          className={clsx("size-2 shrink-0 rounded-full", alarm ? "bg-stop" : tone.dot)}
          aria-hidden="true"
        />
        {alarm ? "Recommendation, with a potential decline" : "Recommendation"}
      </p>
      <p className="mt-2 max-w-[34ch] font-serif text-[1.875rem] leading-[1.2] font-medium text-ink lg:max-w-[40ch]">
        {headline}.{sentence ? <span className="text-graphite"> {sentence}</span> : null}
      </p>
      {real.length ? (
        <ul className="mt-4 space-y-1 text-base">
          {real.map((line, i) => (
            <li
              key={i}
              className={clsx(isDecline(line) ? "font-semibold text-stop" : "text-graphite")}
            >
              {line}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
