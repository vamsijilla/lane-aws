"use client";

import { geoPath } from "d3-geo";
import { useMemo, useState } from "react";
import { feature, mesh } from "topojson-client";
import type { GeometryCollection, Topology } from "topojson-specification";
import atlas from "us-atlas/states-albers-10m.json";

import { useLens } from "@/components/evidence/lens";
import { formatNumber } from "@/lib/format";
import { NAME_BY_CODE, STATES } from "@/lib/us-states";

const topology = atlas as unknown as Topology<{
  states: GeometryCollection;
  nation: GeometryCollection;
}>;
const path = geoPath();
const STEPS = 5;

/**
 * Operating footprint: states shaded by annual IFTA miles (quantized into five steps of the
 * interstate blue), program states outlined in guide green, home state marked. Pre-projected
 * Albers shapes from us-atlas, so nothing is fetched at runtime.
 */
export function FootprintMap({
  miles: annual,
  byQuarter = {},
  quarterIds = {},
  available,
  home,
  stated,
}: {
  miles: Record<string, number>;
  /** Miles by state for each IFTA quarter ('2026-Q1') */
  byQuarter?: Record<string, Record<string, number>>;
  quarterIds?: Record<string, string>;
  available: string[];
  home: string | null;
  stated: string | null;
}) {
  const [hover, setHover] = useState<string | null>(null);
  const [period, setPeriod] = useState<string>("annual");
  const lens = useLens();
  const qs = Object.keys(byQuarter).sort();
  const miles = period === "annual" ? annual : (byQuarter[period] ?? {});
  const openState = (code: string) => {
    const ids = (period === "annual" ? qs : [period])
      .filter((q) => (byQuarter[q]?.[code] ?? 0) > 0)
      .map((q) => quarterIds[q])
      .filter(Boolean);
    if (ids.length)
      lens.open({
        kind: "entity",
        entityIds: ids,
        label: `${NAME_BY_CODE[code] ?? code}: IFTA miles`,
      });
  };
  const shapes = useMemo(() => {
    const states = feature(topology, topology.objects.states);
    return states.features.map((f) => {
      const fips = String(f.id).padStart(2, "0");
      const [code] = STATES[fips] ?? ["", ""];
      return { code, d: path(f) ?? "", centroid: path.centroid(f) };
    });
  }, []);
  const borders = useMemo(
    () => path(mesh(topology, topology.objects.states, (a, b) => a !== b)) ?? "",
    [],
  );
  const max = Math.max(1, ...Object.values(miles));
  const total = Object.values(miles).reduce((a, b) => a + b, 0);
  const step = (m: number) =>
    m <= 0 ? 0 : Math.min(STEPS, Math.ceil((Math.sqrt(m) / Math.sqrt(max)) * STEPS));
  const program = new Set(available);
  const hovered = hover ? shapes.find((s) => s.code === hover) : null;

  return (
    <figure className="relative">
      {qs.length ? (
        <div className="mb-2 flex flex-wrap gap-5" role="group" aria-label="Period">
          {["annual", ...qs].map((p) => (
            <button
              key={p}
              type="button"
              aria-pressed={period === p}
              onClick={() => setPeriod(p)}
              className={
                period === p
                  ? "border-b-2 border-interstate py-1 text-sm text-ink"
                  : "border-b-2 border-transparent py-1 text-sm text-graphite hover:text-ink"
              }
            >
              {p === "annual" ? "Annual" : p.replace(/^(\d{4})-(Q\d)$/, "$2 $1")}
            </button>
          ))}
        </div>
      ) : null}
      <svg
        viewBox="0 0 975 610"
        className="h-auto w-full"
        role="img"
        aria-label="Miles driven by state"
      >
        <defs>
          <pattern
            id="outside-program"
            width="7"
            height="7"
            patternUnits="userSpaceOnUse"
            patternTransform="rotate(45)"
          >
            <line
              x1="0"
              y1="0"
              x2="0"
              y2="7"
              stroke="var(--warning)"
              strokeWidth="1.6"
              opacity="0.7"
            />
          </pattern>
        </defs>
        <g>
          {shapes.map((s) => {
            const m = miles[s.code] ?? 0;
            const k = step(m);
            return (
              <path
                key={s.code || s.d.slice(0, 12)}
                d={s.d}
                fill={
                  k
                    ? `color-mix(in srgb, var(--interstate) ${12 + k * 17}%, var(--surface))`
                    : "var(--surface-sunk)"
                }
                stroke="none"
                onMouseEnter={() => setHover(s.code)}
                onMouseLeave={() => setHover(null)}
                onClick={() => openState(s.code)}
                className={m ? "cursor-pointer hover:brightness-95" : undefined}
              >
                <title>
                  {NAME_BY_CODE[s.code] ?? s.code}:{" "}
                  {m ? `${formatNumber(m)} miles a year` : "no miles"}
                </title>
              </path>
            );
          })}
        </g>
        <g pointerEvents="none">
          {shapes
            .filter((s) => (miles[s.code] ?? 0) > 0 && !program.has(s.code))
            .map((s) => (
              <path key={`o-${s.code}`} d={s.d} fill="url(#outside-program)" />
            ))}
        </g>
        <path d={borders} fill="none" stroke="var(--surface)" strokeWidth={0.9} />
        <g>
          {shapes
            .filter((s) => program.has(s.code))
            .map((s) => (
              <path
                key={`p-${s.code}`}
                d={s.d}
                fill="none"
                stroke="var(--guide)"
                strokeWidth={1.8}
              />
            ))}
        </g>
        {shapes
          .filter((s) => s.code === home || s.code === stated)
          .map((s) => (
            <g key={`m-${s.code}`} transform={`translate(${s.centroid[0]},${s.centroid[1]})`}>
              <circle
                r={5}
                fill={s.code === home ? "var(--ink)" : "var(--warning)"}
                stroke="var(--surface)"
                strokeWidth={1.5}
              />
              <text
                y={-9}
                textAnchor="middle"
                className="fill-ink text-[13px] font-semibold"
                style={{ paintOrder: "stroke", stroke: "var(--surface)", strokeWidth: 3 }}
              >
                {s.code === home ? `${s.code} home` : `${s.code} stated farthest`}
              </text>
            </g>
          ))}
      </svg>
      {hovered ? (
        <div className="pointer-events-none absolute top-2 right-2 rounded-[var(--radius-cell)] border border-hairline bg-surface px-2.5 py-1.5 text-sm shadow-[var(--shadow-overlay)]">
          <p className="font-semibold">{NAME_BY_CODE[hovered.code] ?? hovered.code}</p>
          <p className="tabular-nums text-graphite">
            {formatNumber(miles[hovered.code] ?? 0)} miles{" "}
            {period === "annual" ? "a year" : "this quarter"}
            {total ? `, ${(((miles[hovered.code] ?? 0) / total) * 100).toFixed(1)}%` : ""}
          </p>
          {qs.length ? (
            <ul className="mt-1 text-xs tabular-nums text-graphite">
              {qs.map((q) => (
                <li key={q}>
                  {q.replace(/^(\d{4})-(Q\d)$/, "$2 $1")}:{" "}
                  {formatNumber(byQuarter[q]?.[hovered.code] ?? 0)}
                </li>
              ))}
            </ul>
          ) : null}
          <p className="text-xs text-graphite">
            {program.has(hovered.code) ? "Program state" : "Outside the program states"}
          </p>
        </div>
      ) : null}
      <figcaption className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-graphite">
        <span className="inline-flex items-center gap-1.5">
          <span className="flex" aria-hidden="true">
            {[1, 2, 3, 4, 5].map((k) => (
              <span
                key={k}
                className="h-2.5 w-4"
                style={{
                  background: `color-mix(in srgb, var(--interstate) ${12 + k * 17}%, var(--surface))`,
                }}
              />
            ))}
          </span>
          Fewer to more miles
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="h-2.5 w-4 border-2 border-guide" aria-hidden="true" /> Program states
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span
            className="h-2.5 w-4 bg-[repeating-linear-gradient(45deg,var(--warning)_0_1.5px,transparent_1.5px_5px)]"
            aria-hidden="true"
          />{" "}
          Driven, outside the program
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="size-2.5 rounded-full bg-ink" aria-hidden="true" /> Home state
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="size-2.5 rounded-full bg-warning" aria-hidden="true" /> Farthest
          territory on the application
        </span>
      </figcaption>
    </figure>
  );
}
