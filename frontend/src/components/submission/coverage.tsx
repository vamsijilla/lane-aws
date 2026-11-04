"use client";

import clsx from "clsx";
import { useMemo, useState } from "react";

import { EvidenceLink } from "@/components/evidence/evidence-link";
import { useLens } from "@/components/evidence/lens";
import { FieldValue } from "@/components/submission/field-value";
import { useSubmissionId } from "@/components/submission/shell";
import { Chip, MissingSlot } from "@/components/ui/chips";
import { ErrorNote, Panel, Skeleton } from "@/components/ui/primitives";
import { display, type Entity, field, isoDate, ofKind, one, toNumber } from "@/lib/entities";
import { formatDate, formatMoney, plural } from "@/lib/format";
import { useEntities, useOverview, useRuleset } from "@/lib/queries";

const LINES = [
  ["auto_liability", "Auto liability"],
  ["cargo", "Cargo"],
] as const;

// Carriers are told apart by hue; amber and red stay reserved for warnings and gaps.
const CARRIER_COLORS = ["var(--interstate)", "var(--guide)", "var(--unknown)", "var(--graphite)"];

export function CoverageLosses() {
  const id = useSubmissionId();
  const entities = useEntities(id);
  const overview = useOverview(id);
  const ruleset = useRuleset();
  if (entities.isPending) return <Skeleton lines={10} />;
  if (entities.error)
    return <ErrorNote title="Coverage could not be loaded">{entities.error.message}</ErrorNote>;
  const terms = ofKind(entities.data, "coverage_term");
  const claims = ofKind(entities.data, "claim");
  const history = one(entities.data, "coverage_history");
  const anchorDate = overview.data?.route?.anchor_date ?? new Date().toISOString().slice(0, 10);
  const years = Number(ruleset.data?.params.loss_run_prior_years ?? 3);
  return (
    <div className="space-y-6">
      <Timeline
        terms={terms}
        claims={claims}
        history={history}
        anchorDate={anchorDate}
        lookbackYears={years}
        effectiveDate={overview.data?.applicant.effective_date ?? null}
      />
      <Terms terms={terms} />
      <Losses claims={claims} />
    </div>
  );
}

function Timeline({
  terms,
  claims,
  history,
  anchorDate,
  lookbackYears,
  effectiveDate,
}: {
  effectiveDate: string | null;
  terms: Entity[];
  claims: Entity[];
  history: Entity | undefined;
  anchorDate: string;
  lookbackYears: number;
}) {
  const anchor = isoDate(anchorDate) ?? new Date();
  const effective = isoDate(effectiveDate);
  const today = new Date();
  const lens = useLens();
  const [hover, setHover] = useState<string | null>(null);
  const gaps = history?.summary.history?.gaps ?? [];
  const carriers = useMemo(() => {
    const names: string[] = [];
    for (const t of terms) {
      const c = t.summary.term?.carrier_key ?? "Carrier not shown";
      if (!names.includes(c)) names.push(c);
    }
    return names;
  }, [terms]);

  const dates = terms
    .flatMap((t) => [isoDate(t.summary.term?.start), isoDate(t.summary.term?.end)])
    .filter((d): d is Date => d !== null);
  if (!dates.length) return null;
  const windowStart = new Date(
    anchor.getFullYear() - lookbackYears,
    anchor.getMonth(),
    anchor.getDate(),
  );
  const min = new Date(Math.min(...dates.map((d) => d.getTime()), windowStart.getTime()));
  const max = new Date(
    Math.max(
      ...dates.map((d) => d.getTime()),
      anchor.getTime(),
      effective?.getTime() ?? 0,
      today.getTime(),
    ),
  );
  const start = new Date(min.getFullYear(), min.getMonth() - 2, 1);
  const end = new Date(max.getFullYear(), max.getMonth() + 2, 1);
  const W = 1000;
  const left = 110;
  const lane = 46;
  const top = 34;
  const gapRow = top + LINES.length * lane + 6; // labels for gaps too narrow to hold one
  const H = gapRow + 22 + 24;
  const x = (d: Date) =>
    left + ((d.getTime() - start.getTime()) / (end.getTime() - start.getTime())) * (W - left - 10);
  const years: number[] = [];
  for (let y = start.getFullYear() + 1; y <= end.getFullYear(); y++) years.push(y);

  return (
    <Panel
      title="Coverage timeline"
      description="Policy terms by line and carrier, gaps without coverage, and the three years the guideline looks back."
    >
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="h-auto w-full"
        role="group"
        aria-label="Coverage timeline"
      >
        <defs>
          <pattern
            id="gap-hatch"
            width="6"
            height="6"
            patternUnits="userSpaceOnUse"
            patternTransform="rotate(45)"
          >
            <rect width="6" height="6" fill="var(--stop-soft)" />
            <line
              x1="0"
              y1="0"
              x2="0"
              y2="6"
              stroke="var(--stop)"
              strokeWidth="1.2"
              opacity="0.45"
            />
          </pattern>
        </defs>
        <rect
          x={x(windowStart)}
          y={top - 22}
          width={x(anchor) - x(windowStart)}
          height={H - top + 6}
          fill="var(--interstate-soft)"
          opacity={0.55}
        />
        <text
          x={x(windowStart) + 6}
          y={top - 10}
          className="fill-interstate text-[12px] font-semibold"
        >
          {lookbackYears}-year window to {formatDate(anchorDate)}
        </text>
        {years.map((y) => (
          <g key={y}>
            <line
              x1={x(new Date(y, 0, 1))}
              x2={x(new Date(y, 0, 1))}
              y1={top - 4}
              y2={gapRow - 8}
              stroke="var(--hairline)"
            />
            <text
              x={x(new Date(y, 0, 1))}
              y={H - 8}
              textAnchor="middle"
              className="fill-graphite text-[12px]"
            >
              {y}
            </text>
          </g>
        ))}
        {(
          [
            ["Application", anchor, "var(--interstate)"],
            ["Effective", effective, "var(--guide)"],
            ["Today", today, "var(--graphite)"],
          ] as const
        ).map(([name, d], i) =>
          d ? (
            <g key={name}>
              <line
                x1={x(d)}
                x2={x(d)}
                y1={top - 2}
                y2={gapRow - 8}
                stroke={
                  i === 2 ? "var(--graphite)" : i === 0 ? "var(--interstate)" : "var(--guide)"
                }
                strokeWidth={1.5}
                strokeDasharray={i === 2 ? "2 3" : i === 1 ? "6 3" : undefined}
              />
              <line
                x1={x(d)}
                x2={x(d)}
                y1={top - 2}
                y2={gapRow - 8}
                stroke="transparent"
                strokeWidth={8}
              >
                <title>
                  {name} date: {formatDate(d.toISOString().slice(0, 10))}
                </title>
              </line>
            </g>
          ) : null,
        )}
        {LINES.map(([line, label], li) => {
          const y = top + li * lane;
          return (
            <g key={line}>
              <text x={0} y={y + 21} className="fill-ink text-[13px] font-semibold">
                {label}
              </text>
              {gaps.map((g, gi) => {
                const a = isoDate(g.from);
                const b = isoDate(g.to);
                if (!a || !b) return null;
                const full = `No coverage shown (${g.lines.join(" and ")}), ${g.days} days`;
                const inside = gapLabel(full, `${g.days} days`, x(b) - x(a) - 8);
                return (
                  <g key={`${line}-gap-${gi}`}>
                    <rect
                      x={x(a)}
                      y={y + 6}
                      width={Math.max(2, x(b) - x(a))}
                      height={24}
                      fill="url(#gap-hatch)"
                      rx={2}
                    >
                      <title>{full}</title>
                    </rect>
                    {li === 0 && inside ? (
                      <text
                        x={(x(a) + x(b)) / 2}
                        y={y + 22}
                        textAnchor="middle"
                        className="pointer-events-none fill-stop text-[12px] font-semibold"
                      >
                        {inside}
                      </text>
                    ) : null}
                    {li === 0 && inside !== full ? (
                      // Under the lanes, starting at the gap, cut to the room it has
                      <text
                        x={x(a)}
                        y={gapRow + 12}
                        className="fill-stop text-[12px] font-semibold"
                      >
                        <title>{full}</title>
                        {truncate(full, W - x(a) - 10)}
                      </text>
                    ) : null}
                  </g>
                );
              })}
              {terms
                .filter((t) => t.summary.term?.line === line)
                .map((t) => {
                  const a = isoDate(t.summary.term?.start);
                  const b = isoDate(t.summary.term?.end);
                  if (!a || !b) return null;
                  const carrier = t.summary.term?.carrier_key ?? "Carrier not shown";
                  const color = CARRIER_COLORS[carriers.indexOf(carrier) % CARRIER_COLORS.length];
                  const conflict = field(t, "carrier")?.state === "conflict";
                  return (
                    <g
                      key={t.id}
                      onMouseEnter={() => setHover(t.id)}
                      onMouseLeave={() => setHover(null)}
                      onClick={() =>
                        lens.open({ kind: "entity", entityIds: [t.id], label: t.label })
                      }
                      role="button"
                      tabIndex={0}
                      aria-label={`${t.label}: ${carrier}, ${formatDate(t.summary.term?.start)} to ${formatDate(t.summary.term?.end)}${conflict ? ", carrier disputed" : ""}`}
                      onKeyDown={(e) =>
                        e.key === "Enter" &&
                        lens.open({ kind: "entity", entityIds: [t.id], label: t.label })
                      }
                      className="cursor-pointer outline-none focus-visible:[&>rect]:stroke-interstate"
                    >
                      <rect
                        x={x(a) + 1}
                        y={y + 6}
                        width={Math.max(4, x(b) - x(a) - 2)}
                        height={24}
                        rx={2}
                        fill={color}
                        opacity={hover && hover !== t.id ? 0.55 : 0.9}
                        stroke={conflict ? "var(--warning)" : "none"}
                        strokeWidth={conflict ? 2.5 : 0}
                        strokeDasharray={conflict ? "5 3" : undefined}
                      >
                        <title>
                          {t.label}: {carrier}, {formatDate(t.summary.term?.start)} to{" "}
                          {formatDate(t.summary.term?.end)}
                        </title>
                      </rect>
                      <text
                        x={x(a) + 7}
                        y={y + 22}
                        className="pointer-events-none fill-white text-[11px] font-semibold"
                      >
                        {x(b) - x(a) > 90 ? carrier : ""}
                      </text>
                    </g>
                  );
                })}
              {line === "auto_liability"
                ? claims.map((c) => {
                    const d = isoDate(field(c, "date_of_loss")?.value);
                    if (!d) return null;
                    const lag = c.summary.claim?.report_lag_days;
                    const tip = [
                      `${c.label}: loss on ${formatDate(d.toISOString().slice(0, 10))}`,
                      lag !== null && lag !== undefined ? `reported ${lag} days later` : null,
                      display(c, "status"),
                      display(c, "incurred") ? `${display(c, "incurred")} incurred` : null,
                    ]
                      .filter(Boolean)
                      .join(", ");
                    return (
                      <g
                        key={c.id}
                        transform={`translate(${x(d)},${y + 36})`}
                        role="button"
                        tabIndex={0}
                        aria-label={tip}
                        onClick={() =>
                          lens.open({ kind: "entity", entityIds: [c.id], label: c.label })
                        }
                        onKeyDown={(e) =>
                          e.key === "Enter" &&
                          lens.open({ kind: "entity", entityIds: [c.id], label: c.label })
                        }
                        className="cursor-pointer outline-none"
                      >
                        <rect x={-8} y={-8} width={16} height={16} fill="transparent" />
                        <path d="M0 -5 L5 4 L-5 4 Z" fill="var(--stop)" />
                        <title>{tip}</title>
                      </g>
                    );
                  })
                : null}
            </g>
          );
        })}
      </svg>
      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-graphite">
        {carriers.map((c, i) => (
          <span key={c} className="inline-flex items-center gap-1.5">
            <span
              className="h-2.5 w-4 rounded-[1px]"
              style={{ background: CARRIER_COLORS[i % CARRIER_COLORS.length] }}
            />
            {c}
          </span>
        ))}
        <span className="inline-flex items-center gap-1.5">
          <svg width="12" height="10" aria-hidden="true">
            <path d="M6 1 L11 9 L1 9 Z" fill="var(--stop)" />
          </svg>
          Claim
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2.5 w-4 rounded-[1px] border-2 border-dashed border-warning" /> Carrier
          disputed
        </span>
        {(
          [
            ["Application date", "var(--interstate)", undefined],
            ["Effective date", "var(--guide)", "6 3"],
            ["Today", "var(--graphite)", "2 3"],
          ] as const
        ).map(([name, color, dash]) => (
          <span key={name} className="inline-flex items-center gap-1.5">
            <svg width="14" height="12" aria-hidden="true">
              <line
                x1="7"
                x2="7"
                y1="0"
                y2="12"
                stroke={color}
                strokeWidth="1.5"
                strokeDasharray={dash}
              />
            </svg>
            {name}
          </span>
        ))}
      </div>
    </Panel>
  );
}

function evidenceChip(t: Entity) {
  const term = t.summary.term;
  if (term?.loss_history === "loss_run") return <Chip tone="guide">Loss run</Chip>;
  if (term?.loss_history === "letter")
    return <Chip tone="warning">Letter, {term.letter_scope ?? "partial"}</Chip>;
  return <MissingSlot label="No loss history sent" />;
}

function Terms({ terms }: { terms: Entity[] }) {
  const rows = [...terms].sort(
    (a, b) =>
      (b.summary.term?.start ?? "").localeCompare(a.summary.term?.start ?? "") ||
      (a.summary.term?.line ?? "").localeCompare(b.summary.term?.line ?? ""),
  );
  return (
    <Panel title="Prior terms" flush>
      <table className="w-full text-base">
        <thead>
          <tr className="border-b border-hairline text-left text-sm text-graphite">
            <th className="px-4 py-2 font-normal">Term</th>
            <th className="px-4 py-2 font-normal">Dates</th>
            <th className="px-4 py-2 font-normal">Carrier</th>
            <th className="px-4 py-2 font-normal">Policy</th>
            <th className="px-4 py-2 text-right font-normal">Units</th>
            <th className="px-4 py-2 font-normal">Loss history</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((t) => (
            <tr key={t.id} className="border-b border-hairline last:border-0">
              <td className="px-4 py-2 font-semibold whitespace-nowrap">{t.label}</td>
              <td className="px-4 py-2 text-sm whitespace-nowrap text-graphite tabular-nums">
                {formatDate(t.summary.term?.start)} to {formatDate(t.summary.term?.end)}
                {(t.summary.term?.notes ?? []).map((n) => (
                  <span key={n} className="block max-w-72 text-xs whitespace-normal">
                    {n}
                  </span>
                ))}
              </td>
              <td className="px-4 py-2">
                <FieldValue field={field(t, "carrier")} entity={t} />
              </td>
              <td className="px-4 py-2">
                <FieldValue field={field(t, "policy_number")} entity={t} />
              </td>
              <td className="px-4 py-2 text-right">
                <FieldValue field={field(t, "units")} entity={t} empty="Not shown" />
              </td>
              <td className="px-4 py-2">{evidenceChip(t)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}

function Losses({ claims }: { claims: Entity[] }) {
  const total = (attr: string) =>
    claims.reduce((a, c) => a + (toNumber(field(c, attr)?.value) ?? 0), 0);
  return (
    <Panel
      title="Losses"
      description={`${plural(claims.length, "claim")}, ${formatMoney(total("incurred"))} incurred, ${formatMoney(total("reserved"))} still reserved.`}
      flush
    >
      {claims.length === 0 ? (
        <p className="px-4 py-6 text-graphite">No claims on the loss runs sent.</p>
      ) : (
        <>
          <LossBars claims={claims} />
          <table className="w-full text-base">
            <thead>
              <tr className="border-b border-hairline text-left text-sm text-graphite">
                <th className="px-4 py-2 font-normal">Claim</th>
                <th className="px-4 py-2 font-normal">Loss</th>
                <th className="px-4 py-2 font-normal">Reported</th>
                <th className="px-4 py-2 font-normal">Driver and unit</th>
                <th className="px-4 py-2 font-normal">Status</th>
                <th className="px-4 py-2 text-right font-normal">Paid</th>
                <th className="px-4 py-2 text-right font-normal">Reserved</th>
                <th className="px-4 py-2 text-right font-normal">Incurred</th>
              </tr>
            </thead>
            <tbody>
              {claims.map((c) => {
                const lag = c.summary.claim?.report_lag_days ?? null;
                const open = /open/i.test(display(c, "status") ?? "");
                return (
                  <tr key={c.id} className="border-b border-hairline align-top last:border-0">
                    <td className="px-4 py-2.5">
                      <EvidenceLink
                        target={{ kind: "entity", entityIds: [c.id], label: c.label }}
                        className="font-mono font-semibold"
                      >
                        {c.key}
                      </EvidenceLink>
                      <p className="mt-0.5 max-w-64 text-sm text-graphite">
                        {display(c, "description")}
                      </p>
                    </td>
                    <td className="px-4 py-2.5 tabular-nums">
                      <FieldValue field={field(c, "date_of_loss")} entity={c} />
                      <p className="text-xs text-graphite">{display(c, "location")}</p>
                    </td>
                    <td className="px-4 py-2.5 tabular-nums">
                      <FieldValue field={field(c, "date_reported")} entity={c} />
                      {lag !== null ? (
                        <p
                          className={clsx(
                            "text-xs",
                            c.summary.claim?.reported_late
                              ? "font-semibold text-warning"
                              : "text-graphite",
                          )}
                        >
                          {lag} days after the loss
                        </p>
                      ) : null}
                    </td>
                    <td className="px-4 py-2.5 text-sm">
                      <p className="flex flex-wrap items-center gap-1.5">
                        <FieldValue field={field(c, "driver")} entity={c} />
                        {c.summary.claim?.driver_reported === false ? (
                          <Chip tone="stop">Not on schedule</Chip>
                        ) : null}
                      </p>
                      <p className="mt-1 flex flex-wrap items-center gap-1.5">
                        {/* The unit as the board settled it (by VIN); the loss run's own
                            wording is in the evidence */}
                        {c.summary.claim?.unit ? (
                          <EvidenceLink
                            target={{
                              kind: "field",
                              fieldId: field(c, "vehicle")?.id ?? "",
                              label: `${c.label}: Unit`,
                            }}
                          >
                            {c.summary.claim.unit}
                          </EvidenceLink>
                        ) : (
                          <FieldValue field={field(c, "vehicle")} entity={c} />
                        )}
                        {c.summary.claim?.unit_reported === false ? (
                          <Chip tone="stop">Not on schedule</Chip>
                        ) : null}
                      </p>
                    </td>
                    <td className="px-4 py-2.5">
                      <Chip tone={open ? "warning" : "neutral"}>
                        {display(c, "status") ?? "Unknown"}
                      </Chip>
                    </td>
                    <td className="px-4 py-2.5 text-right tabular-nums">
                      <FieldValue field={field(c, "paid")} entity={c} />
                    </td>
                    <td
                      className={clsx(
                        "px-4 py-2.5 text-right tabular-nums",
                        open && "font-semibold",
                      )}
                    >
                      <FieldValue field={field(c, "reserved")} entity={c} />
                    </td>
                    <td className="px-4 py-2.5 text-right font-semibold tabular-nums">
                      <FieldValue field={field(c, "incurred")} entity={c} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </>
      )}
    </Panel>
  );
}

type Part = { what: string; amount: number; reserve: boolean; expense: boolean };

function money(text: unknown): number {
  const n = Number(String(text ?? "").replace(/[^0-9.-]/g, ""));
  return Number.isFinite(n) ? n : 0;
}

/** Each claim as one bar on a shared scale: paid solid, still reserved hatched, expenses grey. */
function LossBars({ claims }: { claims: Entity[] }) {
  const lens = useLens();
  const rows = claims.map((c) => {
    const lines = (c.details.lines ?? []) as {
      coverage?: string;
      paid?: string;
      reserves?: string;
    }[];
    const parts: Part[] = lines.flatMap((l) => {
      const expense = /expense/i.test(l.coverage ?? "");
      const what = l.coverage ?? "Loss";
      return [
        { what, amount: money(l.paid), reserve: false, expense },
        { what, amount: money(l.reserves), reserve: true, expense },
      ].filter((p) => p.amount > 0);
    });
    if (!parts.length) {
      parts.push(
        {
          what: "Paid",
          amount: toNumber(field(c, "paid")?.value) ?? 0,
          reserve: false,
          expense: false,
        },
        {
          what: "Reserved",
          amount: toNumber(field(c, "reserved")?.value) ?? 0,
          reserve: true,
          expense: false,
        },
      );
    }
    return { c, parts, total: parts.reduce((a, p) => a + p.amount, 0) };
  });
  const max = Math.max(1, ...rows.map((r) => r.total));
  return (
    <div className="space-y-3 border-b border-hairline px-4 py-4">
      {rows.map(({ c, parts, total }) => {
        const open = /open/i.test(display(c, "status") ?? "");
        const lag = c.summary.claim?.report_lag_days;
        return (
          <div key={c.id} className="grid grid-cols-[9rem_1fr] items-center gap-3">
            <div className="text-sm">
              <EvidenceLink
                target={{ kind: "entity", entityIds: [c.id], label: c.label }}
                className="font-mono font-semibold"
              >
                {c.key}
              </EvidenceLink>
              <p className="flex flex-wrap gap-1 text-xs">
                <Chip tone={open ? "warning" : "neutral"}>{open ? "Open" : "Closed"}</Chip>
                {lag !== null && lag !== undefined ? (
                  <span
                    className={
                      c.summary.claim?.reported_late
                        ? "font-semibold text-warning"
                        : "text-graphite"
                    }
                  >
                    {lag} days late
                  </span>
                ) : null}
              </p>
            </div>
            <div>
              <button
                type="button"
                onClick={() => lens.open({ kind: "entity", entityIds: [c.id], label: c.label })}
                className="flex h-6 w-full overflow-hidden rounded-[4px] bg-surface-sunk"
                aria-label={`${c.label}: ${formatMoney(total)} incurred. ${parts
                  .map(
                    (p) => `${p.what} ${p.reserve ? "reserved" : "paid"} ${formatMoney(p.amount)}`,
                  )
                  .join("; ")}`}
              >
                {parts.map((p, i) => (
                  <span
                    key={i}
                    title={`${p.what}, ${p.reserve ? "still reserved" : "paid"}: ${formatMoney(p.amount)}`}
                    className={clsx(
                      "h-full border-r border-surface last:border-r-0",
                      BAR[`${p.expense ? "e" : "l"}${p.reserve ? "r" : "p"}`],
                    )}
                    style={{ width: `${(p.amount / max) * 100}%` }}
                  />
                ))}
              </button>
              <p className="mt-0.5 flex flex-wrap gap-x-3 text-xs text-graphite tabular-nums">
                <span className="font-semibold text-ink">{formatMoney(total)} incurred</span>
                {c.summary.claim?.driver_reported === false ? (
                  <span className="font-semibold text-stop">Driver not on schedule</span>
                ) : null}
                {c.summary.claim?.unit_reported === false ? (
                  <span className="font-semibold text-stop">Unit not on schedule</span>
                ) : null}
              </p>
            </div>
          </div>
        );
      })}
      <p className="flex flex-wrap gap-x-4 text-xs text-graphite">
        {(
          [
            ["lp", "Loss paid"],
            ["lr", "Loss still reserved"],
            ["ep", "Expense paid (legal, adjusting)"],
            ["er", "Expense still reserved"],
          ] as const
        ).map(([k, label]) => (
          <span key={k} className="inline-flex items-center gap-1.5">
            <span className={clsx("h-2.5 w-4 rounded-[1px]", BAR[k])} /> {label}
          </span>
        ))}
      </p>
    </div>
  );
}

// Paid is solid, reserved is hatched; losses in red, expenses in grey (shape and color both tell)
const BAR: Record<string, string> = {
  lp: "bg-stop",
  lr: "bg-[repeating-linear-gradient(45deg,var(--stop)_0_3px,var(--stop-soft)_3px_6px)]",
  ep: "bg-graphite",
  er: "bg-[repeating-linear-gradient(45deg,var(--graphite)_0_3px,var(--surface-sunk)_3px_6px)]",
};

const CHAR = 6.6; // average width of a 12px semibold character, in viewBox units

/** The label that fits inside a gap of `room` units: the full one, a short one, or none. */
function gapLabel(full: string, short: string, room: number): string | null {
  if (full.length * CHAR <= room) return full;
  if (short.length * CHAR <= room) return short;
  return null;
}

function truncate(text: string, room: number): string {
  const n = Math.floor(room / CHAR);
  return text.length <= n ? text : `${text.slice(0, Math.max(1, n - 1))}…`;
}
