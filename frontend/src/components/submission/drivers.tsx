"use client";

import clsx from "clsx";
import { Crown, UserRoundX } from "lucide-react";

import { EvidenceLink } from "@/components/evidence/evidence-link";
import { useLens } from "@/components/evidence/lens";
import { PageImage } from "@/components/evidence/page-image";
import { FieldValue } from "@/components/submission/field-value";
import { useSubmissionId } from "@/components/submission/shell";
import { Chip, MissingSlot } from "@/components/ui/chips";
import { ErrorNote, Identifier, Panel, Skeleton } from "@/components/ui/primitives";
import { type Entity, type Field, field, ofKind } from "@/lib/entities";
import { formatDate } from "@/lib/format";
import { useDocuments, useEntities, useOverview } from "@/lib/queries";
import { useSession } from "@/lib/session";

// Column keys match the API's source_rows; the API decides which documents feed each one.
const COLUMNS: { key: string; label: string }[] = [
  { key: "schedule", label: "Schedule and forms" },
  { key: "mvr", label: "Motor vehicle record" },
  { key: "license", label: "License card" },
];

const ATTRS: [string, string][] = [
  ["date_of_birth", "Date of birth"],
  ["license_number", "License number"],
  ["license_state", "License state"],
  ["license_class", "Class"],
  ["commercial_license", "Commercial license"],
  ["license_status", "Status"],
  ["license_issued", "Issued"],
  ["license_expiration", "Expires"],
  ["first_licensed", "First licensed"],
  ["experience_stated", "Experience stated"],
  ["hire_date", "Hired"],
];

export function Drivers() {
  const id = useSubmissionId();
  const entities = useEntities(id);
  if (entities.isPending) return <Skeleton lines={12} />;
  if (entities.error)
    return <ErrorNote title="Drivers could not be loaded">{entities.error.message}</ErrorNote>;
  const drivers = ofKind(entities.data, "driver");
  const scheduled = drivers.filter((d) => d.summary.driver?.scheduled);
  const elsewhere = drivers.filter((d) => !d.summary.driver?.scheduled);
  return (
    <div className="space-y-6">
      <p className="text-graphite">
        {scheduled.length} scheduled drivers. Each card compares what the schedule, the motor
        vehicle record and the license card say; disagreements are highlighted.
      </p>
      {scheduled.map((d) => (
        <DriverCard key={d.id} d={d} />
      ))}
      {elsewhere.length ? (
        <section>
          <h2 className="mb-2 flex items-center gap-2 font-display text-md font-semibold">
            <UserRoundX size={16} className="text-stop" aria-hidden="true" />
            Found only in loss history
          </h2>
          <div className="space-y-3">
            {elsewhere.map((d) => (
              <Panel key={d.id}>
                <p className="flex flex-wrap items-center gap-2">
                  <EvidenceLink
                    target={{ kind: "entity", entityIds: [d.id], label: d.label }}
                    className="font-display text-lg font-bold"
                  >
                    {d.label}
                  </EvidenceLink>
                  <Chip tone="stop">Not on any driver schedule</Chip>
                </p>
                <p className="mt-1 text-sm text-graphite">
                  Drove in claim {d.summary.driver?.claims.join(", ")}
                  {d.summary.driver?.reported_to_prior_carrier === false
                    ? ", not reported to the prior carrier as a driver"
                    : ""}
                  . No motor vehicle record was sent.
                </p>
              </Panel>
            ))}
          </div>
        </section>
      ) : null}
    </div>
  );
}

function DriverCard({ d }: { d: Entity }) {
  const s = d.summary.driver;
  if (!s) return null;
  const mvr = s.mvr;
  const rows = new Map(s.source_rows.map((r) => [r.attribute, r]));
  return (
    <section id={`driver-${d.id}`} className="scroll-mt-40">
      <Panel flush>
        <div className="flex items-start gap-4 border-b border-hairline px-4 py-3">
          <LicensePhoto documentId={s.license_card?.document_id ?? null} name={d.label} />
          <div className="min-w-0 flex-1">
            <h2 className="flex flex-wrap items-center gap-2">
              <EvidenceLink
                target={{ kind: "field", fieldId: field(d, "name")?.id ?? "", label: d.label }}
                className="font-display text-lg font-bold"
              >
                {d.label}
              </EvidenceLink>
              {s.owner ? (
                <Chip tone="interstate" icon={Crown}>
                  Owner
                </Chip>
              ) : null}
              {excludedState(d)}
            </h2>
            <p className="mt-1 flex flex-wrap gap-1.5">
              {s.badges.map((b) => (
                <Chip key={b.text} tone={b.tone}>
                  {b.text}
                </Chip>
              ))}
            </p>
            {s.earliest_cdl ? <p className="mt-1 text-sm text-graphite">{s.earliest_cdl}</p> : null}
          </div>
        </div>
        <table className="w-full table-fixed text-sm">
          <thead>
            <tr className="border-b border-hairline text-left text-graphite">
              <th className="w-40 px-4 py-2 font-normal">Field</th>
              {COLUMNS.map((c) => (
                <th key={c.key} className="px-4 py-2 font-normal">
                  {c.label}
                </th>
              ))}
              <th className="px-4 py-2 font-normal">Resolved</th>
            </tr>
          </thead>
          <tbody>
            {ATTRS.filter(([a]) => field(d, a)).map(([attr, label]) => {
              const row = rows.get(attr);
              const cols = row?.columns ?? {};
              const differs = row?.differs ?? false;
              const mono = attr === "license_number" || attr === "license_class";
              return (
                <tr
                  key={attr}
                  className={clsx(
                    "border-b border-hairline/70 last:border-0",
                    differs && "bg-warning-soft/50",
                  )}
                >
                  <td className="px-4 py-1.5 text-graphite">
                    {label}
                    {attr === "date_of_birth" ? (
                      <span className="block text-xs text-graphite">Years hidden for privacy</span>
                    ) : null}
                  </td>
                  {COLUMNS.map((c) => (
                    <td
                      key={c.key}
                      className={clsx(
                        "px-4 py-1.5",
                        differs && cols[c.key] && "font-semibold text-warning",
                      )}
                    >
                      {cols[c.key]?.length ? (
                        cols[c.key].map((v) => {
                          const old = v.endsWith(" (superseded)");
                          const text = old ? v.slice(0, -" (superseded)".length) : v;
                          return (
                            <span key={v} className={clsx("block", old && "text-graphite")}>
                              {mono ? <Identifier value={text} /> : text}
                              {old ? (
                                <span className="block text-xs font-normal text-graphite">
                                  {HISTORY.has(attr)
                                    ? "Superseded, still used as history"
                                    : "Superseded by a newer record"}
                                </span>
                              ) : null}
                            </span>
                          );
                        })
                      ) : (
                        <span className="text-graphite">Not shown</span>
                      )}
                    </td>
                  ))}
                  <td className="px-4 py-1.5">
                    <Resolved field={field(d, attr)} entity={d} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {mvr ? <PointsStrip d={d} /> : null}
        {mvr ? (
          <div className="grid grid-cols-1 gap-4 border-t border-hairline px-4 py-3 lg:grid-cols-2">
            <div>
              <p className="mb-1 text-sm font-semibold text-graphite">
                Record {mvr.record_type ? `(${mvr.record_type.toLowerCase()})` : ""}, ordered{" "}
                {formatDate(mvr.order_date)}
              </p>
              {mvr.events.length ? (
                <ul className="space-y-1 text-sm">
                  {mvr.events.map((e, i) => (
                    <li key={i} className="grid grid-cols-[6.5rem_1fr_4rem] gap-2">
                      <span className="tabular-nums text-graphite">{formatDate(e.event_date)}</span>
                      <span>
                        {eventLabel(e.kind, e.description) ? (
                          <span className="font-semibold">
                            {eventLabel(e.kind, e.description)}{" "}
                          </span>
                        ) : null}
                        {e.description}
                        {e.location ? <span className="text-graphite">, {e.location}</span> : null}
                      </span>
                      <span className="text-right tabular-nums">
                        {e.points ? `${e.points} pts` : ""}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-guide">No violations or accidents on the record.</p>
              )}
            </div>
            <div>
              <p className="mb-1 text-sm font-semibold text-graphite">License history notes</p>
              {mvr.notes.length ? (
                <ul className="space-y-1 text-sm">
                  {mvr.notes.map((n, i) => (
                    <li key={i} className="grid grid-cols-[6.5rem_1fr] gap-2">
                      <span className="tabular-nums text-graphite">{formatDate(n.note_date)}</span>
                      <span className={clsx(n.flagged && "font-semibold text-warning")}>
                        {n.text}
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <MissingSlot label="None on the record" />
              )}
            </div>
          </div>
        ) : (
          <p className="border-t border-hairline px-4 py-3 text-sm text-stop">
            No motor vehicle record was sent for this driver.
          </p>
        )}
      </Panel>
    </section>
  );
}

function LicensePhoto({ documentId, name }: { documentId: string | null; name: string }) {
  const id = useSubmissionId();
  const docs = useDocuments(id);
  const lens = useLens();
  const session = useSession();
  const doc = documentId ? docs.data?.find((x) => x.id === documentId) : undefined;
  const pageId = doc?.page_ids[0];
  if (!pageId || !session.can("view_content")) {
    return (
      <div className="flex h-16 w-24 shrink-0 items-center justify-center rounded-[var(--radius-cell)] border border-dashed border-hairline text-[11px] text-graphite">
        No license photo
      </div>
    );
  }
  return (
    <button
      type="button"
      data-evidence=""
      onClick={() => lens.open({ kind: "page", pageId, label: doc.title })}
      className="shrink-0 overflow-hidden rounded-[var(--radius-cell)] border border-hairline hover:border-interstate"
      title={`License card for ${name}`}
    >
      <PageImage
        pageId={pageId}
        variant="thumb"
        personal
        alt={`License card, ${name} (blurred until revealed)`}
        className="h-16 w-24 object-cover"
      />
    </button>
  );
}

const EVENT_LABELS: Record<string, string> = {
  violation: "Violation",
  conviction: "Conviction",
  accident: "Accident",
  suspension: "Suspension",
};

/** The event's kind, unless its description already says it ("Accident", "CDL renewal"). */
function eventLabel(kind: string | null | undefined, description: string | null | undefined) {
  const label = kind ? EVENT_LABELS[kind] : undefined;
  if (!label) return null;
  return description?.toLowerCase().startsWith(label.toLowerCase()) ? null : label;
}

/** Fields an older record still answers: the earliest CDL evidence. */
const HISTORY = new Set([
  "license_issued",
  "first_licensed",
  "commercial_license",
  "license_class",
]);

/** The resolved value; a field the documents disagree on says so and offers to settle it. */
function Resolved({ field: f, entity }: { field: Field | undefined; entity: Entity }) {
  const lens = useLens();
  if (f?.state !== "conflict") return <FieldValue field={f} entity={entity} />;
  const n = f.candidates.filter((c) => !c.superseded).length;
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <span className="font-semibold text-warning">Unresolved, {n} values</span>
      <button
        type="button"
        onClick={() =>
          lens.open({ kind: "field", fieldId: f.id, label: `${entity.label}: ${f.label}` })
        }
        className="rounded-[var(--radius-cell)] border border-hairline px-2 py-0.5 text-xs font-semibold text-interstate hover:border-interstate"
      >
        Resolve
      </button>
    </span>
  );
}

/**
 * Five years of the driver's record before the application date: the 3-year and 12-month
 * windows the guideline counts points in, and each violation as a dot sized by its points.
 */
function PointsStrip({ d }: { d: Entity }) {
  const id = useSubmissionId();
  const overview = useOverview(id);
  const lens = useLens();
  const mvr = d.summary.driver?.mvr;
  const anchorIso = overview.data?.route?.anchor_date;
  if (!mvr || !anchorIso) return null;
  const anchor = new Date(`${anchorIso}T00:00:00`);
  const back = (years: number) =>
    new Date(anchor.getFullYear() - years, anchor.getMonth(), anchor.getDate());
  const start = back(5);
  const W = 1000;
  const H = 56;
  const pad = 8;
  const x = (t: Date) =>
    pad + ((t.getTime() - start.getTime()) / (anchor.getTime() - start.getTime())) * (W - 2 * pad);
  const events = mvr.events
    .map((e) => ({ e, at: e.event_date ? new Date(`${isoFrom(e.event_date)}T00:00:00`) : null }))
    .filter(
      (v): v is { e: (typeof mvr.events)[number]; at: Date } => v.at !== null && v.at >= start,
    );
  const three = back(3);
  const twelve = back(1);
  const years = [5, 4, 3, 2, 1, 0].map(back);
  return (
    <div className="border-t border-hairline px-4 py-3">
      <p className="mb-1 text-sm font-semibold text-graphite">
        Points window: 5 years to the application date
      </p>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="h-auto w-full"
        role="group"
        aria-label={`Violations for ${d.label} over 5 years`}
      >
        <rect
          x={x(three)}
          y={6}
          width={x(anchor) - x(three)}
          height={30}
          fill="var(--interstate-soft)"
        />
        <rect
          x={x(twelve)}
          y={6}
          width={x(anchor) - x(twelve)}
          height={30}
          fill="var(--interstate)"
          opacity={0.18}
        />
        <text x={x(three) + 4} y={18} className="fill-interstate text-[11px] font-semibold">
          3 years
        </text>
        <text x={x(twelve) + 4} y={32} className="fill-interstate text-[11px] font-semibold">
          12 months
        </text>
        <line x1={pad} x2={W - pad} y1={36} y2={36} stroke="var(--hairline)" />
        {years.map((y, i) => (
          <text
            key={y.getTime()}
            x={x(y)}
            y={52}
            textAnchor={i === 0 ? "start" : i === years.length - 1 ? "end" : "middle"}
            className="fill-graphite text-[11px]"
          >
            {y.getFullYear()}
          </text>
        ))}
        {events.map(({ e, at }, i) => {
          const pts = e.points ?? 0;
          const inside = at >= three;
          const tip = `${formatDate(isoFrom(e.event_date ?? ""))}: ${e.description ?? e.kind ?? "Event"}, ${
            pts ? `${pts} points` : "no points"
          }${inside ? "" : ", outside the 3-year window"}. Source: ${mvr.title}`;
          return (
            <g
              key={i}
              role="button"
              tabIndex={0}
              aria-label={tip}
              className="cursor-pointer outline-none"
              onClick={() => lens.open({ kind: "entity", entityIds: [d.id], label: d.label })}
              onKeyDown={(ev) =>
                ev.key === "Enter" &&
                lens.open({ kind: "entity", entityIds: [d.id], label: d.label })
              }
            >
              <circle
                cx={x(at)}
                cy={22}
                r={4 + pts * 1.5}
                fill={inside ? "var(--stop)" : "var(--surface)"}
                stroke="var(--stop)"
                strokeWidth={2}
              />
              {pts ? (
                <text
                  x={x(at)}
                  y={25}
                  textAnchor="middle"
                  className={
                    inside
                      ? "fill-white text-[10px] font-semibold"
                      : "fill-stop text-[10px] font-semibold"
                  }
                >
                  {pts}
                </text>
              ) : null}
              <title>{tip}</title>
            </g>
          );
        })}
      </svg>
      {events.length === 0 ? (
        <p className="text-sm text-guide">No violations in the last 5 years.</p>
      ) : (
        <p className="text-xs text-graphite">
          Filled dots count toward the guideline; hollow dots fall outside the 3-year window. Dot
          size is points.
        </p>
      )}
    </div>
  );
}

/** '06/07/2023' or '2023-06-07' -> '2023-06-07'. */
function isoFrom(text: string): string {
  const m = /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/.exec(text.trim());
  return m ? `${m[3]}-${m[1].padStart(2, "0")}-${m[2].padStart(2, "0")}` : text.slice(0, 10);
}

/** A driver excluded by name: the same state the rules and the counts use. */
function excludedState(d: Entity) {
  const ex = (d.details as Record<string, unknown>).excluded as
    { status?: string; title?: string } | undefined;
  if (!ex) return null;
  const pending = ex.status !== "confirmed";
  return (
    <Chip tone={pending ? "warning" : "neutral"} title={ex.title}>
      {pending ? "Excluded by name, pending confirmation" : "Excluded by name"}
    </Chip>
  );
}
