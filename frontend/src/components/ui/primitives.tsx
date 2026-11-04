import clsx from "clsx";
import { CircleAlert, Loader2, type LucideIcon } from "lucide-react";

/** Panels: by default a section under a hairline divider, no box, so a page reads as one document.
 * `boxed`: a card, kept for the few things that are a single object to act on (the decision). */
export function Panel({
  title,
  description,
  actions,
  children,
  className,
  flush,
  boxed,
}: {
  title?: React.ReactNode;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  flush?: boolean;
  boxed?: boolean;
}) {
  const plain = !boxed;
  return (
    <section
      className={clsx(
        plain
          ? // Tables line up with the divider: no inset on the outer columns
            "border-t border-hairline pt-5 [&_td:first-child]:pl-0 [&_td:last-child]:pr-0 [&_th:first-child]:pl-0 [&_th:last-child]:pr-0"
          : "rounded-[var(--radius-panel)] border border-hairline",
        className,
      )}
    >
      {title ? (
        <header
          className={clsx(
            "flex items-start justify-between gap-4",
            plain ? "pb-3" : "border-b border-hairline px-5 py-3.5",
          )}
        >
          <div className="min-w-0">
            <h2 className="font-display text-md font-semibold tracking-tight">{title}</h2>
            {description ? <p className="mt-0.5 text-sm text-graphite">{description}</p> : null}
          </div>
          {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
        </header>
      ) : null}
      <div className={flush || plain ? undefined : "p-5"}>{children}</div>
    </section>
  );
}

type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "quiet";
  icon?: LucideIcon;
  busy?: boolean;
  size?: "sm" | "md";
};

export function Button({
  variant = "secondary",
  icon: Icon,
  busy,
  size = "md",
  className,
  children,
  disabled,
  ...rest
}: ButtonProps) {
  return (
    <button
      type="button"
      disabled={disabled || busy}
      className={clsx(
        "inline-flex items-center justify-center gap-1.5 rounded-[var(--radius-control)] font-semibold whitespace-nowrap transition-colors disabled:cursor-not-allowed disabled:opacity-50",
        size === "sm" ? "h-7 px-2 text-sm" : "h-8 px-3 text-base",
        variant === "primary" && "bg-interstate text-on-interstate hover:bg-interstate/90",
        variant === "secondary" &&
          "border border-hairline bg-surface text-ink hover:bg-surface-sunk",
        variant === "quiet" && "text-interstate hover:bg-interstate-soft",
        className,
      )}
      {...rest}
    >
      {busy ? (
        <Loader2 size={14} className="animate-spin" aria-hidden="true" />
      ) : Icon ? (
        <Icon size={14} strokeWidth={2.2} aria-hidden="true" />
      ) : null}
      {children}
    </button>
  );
}

export function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <kbd className="inline-flex h-5 min-w-5 items-center justify-center rounded-[4px] border border-hairline bg-surface px-1 font-mono text-[11px] text-graphite shadow-[0_1px_0_var(--hairline)]">
      {children}
    </kbd>
  );
}

/**
 * Identifiers where every character matters (VINs, license and policy numbers, DOT and MC):
 * a monospace face with a slashed zero, so a license that starts with the letter O reads right.
 */
export function Identifier({ value, className }: { value: string; className?: string }) {
  return (
    <span
      className={clsx(
        "font-mono text-[0.92em] tracking-wide [font-feature-settings:'zero']",
        className,
      )}
    >
      {value}
    </span>
  );
}

export function Empty({
  icon: Icon,
  title,
  children,
}: {
  icon?: LucideIcon;
  title: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center gap-2 px-6 py-10 text-center">
      {Icon ? <Icon size={20} className="text-graphite" aria-hidden="true" /> : null}
      <p className="font-semibold">{title}</p>
      {children ? <div className="max-w-md text-sm text-graphite">{children}</div> : null}
    </div>
  );
}

export function ErrorNote({
  title,
  children,
  next = "Reload the page to try again. If it keeps failing, Operations shows whether the service is up.",
}: {
  title: string;
  children?: React.ReactNode;
  /** What the person can do about it */
  next?: string;
}) {
  return (
    <div
      role="alert"
      className="flex gap-2.5 rounded-[var(--radius-panel)] border border-stop/30 bg-stop-soft px-4 py-3 text-sm"
    >
      <CircleAlert size={16} className="mt-0.5 shrink-0 text-stop" aria-hidden="true" />
      <div>
        <p className="font-semibold text-stop">{title}</p>
        {children ? <div className="mt-0.5 text-ink">{children}</div> : null}
        <p className="mt-1 text-graphite">{next}</p>
      </div>
    </div>
  );
}

/** Skeleton lines that match the final layout's rhythm. */
export function Skeleton({ lines = 3, className }: { lines?: number; className?: string }) {
  return (
    <div className={clsx("space-y-2.5", className)} aria-busy="true" aria-label="Loading">
      {Array.from({ length: lines }, (_, i) => (
        <div
          key={i}
          className="h-3.5 animate-pulse rounded-[4px] bg-surface-sunk"
          style={{ width: `${92 - ((i * 17) % 40)}%` }}
        />
      ))}
    </div>
  );
}

export function Label({ children }: { children: React.ReactNode }) {
  return <span className="text-sm text-graphite">{children}</span>;
}

/** A definition list row: label on the left, value on the right, aligned on a hairline grid. */
export function Row({
  label,
  children,
  hint,
  top,
}: {
  label: React.ReactNode;
  children: React.ReactNode;
  hint?: React.ReactNode;
  /** Label and value aligned at their tops, for long labels that wrap (form questions) */
  top?: boolean;
}) {
  return (
    <div
      className={clsx(
        "grid grid-cols-[minmax(9rem,38%)_1fr] gap-3 border-b border-hairline/70 py-2 last:border-0",
        top ? "items-start" : "items-baseline",
      )}
    >
      <dt className="text-sm text-graphite">{label}</dt>
      <dd className="min-w-0 [overflow-wrap:anywhere]">
        {children}
        {hint ? <p className="mt-0.5 text-xs text-graphite">{hint}</p> : null}
      </dd>
    </div>
  );
}

export function PageTitle({
  title,
  description,
  actions,
}: {
  title: string;
  description?: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <div className="mb-5 flex items-end justify-between gap-4">
      <div>
        <h1 className="font-serif text-2xl font-medium">{title}</h1>
        {description ? <p className="mt-1 text-base text-graphite">{description}</p> : null}
      </div>
      {actions}
    </div>
  );
}

/** Secondary text: dates, sources, rule ids. One quiet line, no separators. */
export function Meta({ children, className }: { children: React.ReactNode; className?: string }) {
  return <span className={clsx("text-sm text-graphite", className)}>{children}</span>;
}

/** Header row class for quiet tables: plain graphite labels on a hairline, no fill, no caps. */
export const TABLE_HEAD = "border-b border-hairline text-left text-sm text-graphite";
