"use client";

import { CircleCheck, CircleSlash, TriangleAlert } from "lucide-react";

import { EvidenceLink } from "@/components/evidence/evidence-link";
import type { EvidenceTarget } from "@/components/evidence/lens";
import { FieldValue } from "@/components/submission/field-value";
import { FootprintMap } from "@/components/submission/footprint-map";
import { useSubmissionId } from "@/components/submission/shell";
import { Chip } from "@/components/ui/chips";
import { ErrorNote, Panel, Row, Skeleton } from "@/components/ui/primitives";
import type { Schemas } from "@/lib/api";
import { type Entity, field, ofKind, one } from "@/lib/entities";
import { formatMoney, formatNumber, formatQuarter } from "@/lib/format";
import { useDocumentFacts, useEntities, useExtractions, useRuleset } from "@/lib/queries";
import { NAME_BY_CODE } from "@/lib/us-states";

const APPLICANT = [
  ["legal_name", "Legal name"],
  ["entity_form", "Entity form"],
  ["dot_number", "USDOT number"],
  ["mc_number", "MC number"],
  ["fein", "FEIN"],
  ["mailing_address", "Mailing address"],
  ["mailing_city", "City"],
  ["mailing_state", "State"],
  ["contact_name", "Contact"],
  ["phone", "Phone"],
  ["email", "Email"],
  ["years_experience", "Years in business"],
  ["years_prior_insurance", "Years of prior insurance"],
  ["application_date", "Application date"],
  ["effective_date", "Requested effective date"],
] as const;

const AGENCY = [
  ["name", "Agency"],
  ["contact", "Contact"],
  ["phone", "Phone"],
  ["email", "Email"],
] as const;

export function Operations() {
  const id = useSubmissionId();
  const entities = useEntities(id);
  const ruleset = useRuleset();
  if (entities.isPending) return <Skeleton lines={12} />;
  if (entities.error)
    return <ErrorNote title="Facts could not be loaded">{entities.error.message}</ErrorNote>;
  const all = entities.data;
  const applicant = one(all, "applicant");
  const agency = one(all, "agency");
  const ops = one(all, "operations");
  const request = one(all, "coverage_request");
  const summary = ops?.summary.operations;
  const miles = summary?.miles_by_state ?? {};
  const home = summary?.domicile ?? null;
  const stated = field(ops, "farthest_territory");
  const statedCodes = stated?.candidates.map((c) => String(c.display ?? "")) ?? [];
  const farthest = summary?.farthest_state;
  const available = (ruleset.data?.params.available_states ?? []) as string[];
  const ranked = Object.entries(miles); // largest first, from the API
  const quarters = ofKind(all, "ifta_quarter").sort((a, b) => a.key.localeCompare(b.key));
  const byQuarter = Object.fromEntries(
    quarters.map((q) => [
      q.key,
      Object.fromEntries(
        Object.entries((q.details.miles_by_state ?? {}) as Record<string, string>).map(([k, v]) => [
          k,
          Number(v) || 0,
        ]),
      ),
    ]),
  );
  const totalMiles = summary?.annual_miles ?? 0;

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <Panel title="Applicant">
          <dl>
            {APPLICANT.map(([attr, label]) => (
              <Row key={attr} label={label}>
                <FieldValue field={field(applicant, attr)} entity={applicant} />
              </Row>
            ))}
          </dl>
        </Panel>
        <div className="space-y-6">
          <Panel title="Agency">
            <dl>
              {AGENCY.map(([attr, label]) => (
                <Row key={attr} label={label}>
                  <FieldValue field={field(agency, attr)} entity={agency} />
                </Row>
              ))}
            </dl>
          </Panel>
          <Panel title="Coverage requested">
            <dl>
              {(
                [
                  ["auto_liability_limit", "Auto liability limit"],
                  ["auto_liability_deductible", "Liability deductible"],
                  ["um_limit", "Uninsured motorist"],
                  ["cargo_limit", "Cargo limit"],
                  ["physical_damage_requested", "Physical damage requested"],
                ] as const
              ).map(([attr, label]) => (
                <Row key={attr} label={label}>
                  <FieldValue field={field(request, attr)} entity={request} />
                </Row>
              ))}
            </dl>
          </Panel>
        </div>
      </div>

      <Panel
        title="Operating footprint"
        description="Annual miles by state from the IFTA returns, against the program's available states."
      >
        <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1fr)_17rem]">
          <FootprintMap
            byQuarter={byQuarter}
            quarterIds={Object.fromEntries(quarters.map((q) => [q.key, q.id]))}
            miles={miles}
            available={available}
            home={home}
            stated={statedCodes.length ? statedCodes[0] : null}
          />
          <div className="space-y-4">
            <dl className="space-y-2.5">
              <Stat label="Annual miles" value={formatNumber(totalMiles)} />
              <Stat
                label={`Outside ${home ?? "home state"}`}
                value={
                  summary?.share_outside_home_pct != null
                    ? `${formatNumber(summary.share_outside_home_pct)}%`
                    : ""
                }
              />
              <Stat label="States driven" value={String(ranked.length)} />
              {farthest ? (
                <Stat
                  label="Farthest state driven"
                  value={NAME_BY_CODE[farthest.code] ?? farthest.code}
                  note={`${formatNumber(farthest.miles)} miles, about ${formatNumber(farthest.distance)} miles from home`}
                />
              ) : null}
              <div>
                <dt className="text-sm text-graphite">Farthest territory on the application</dt>
                <dd className="mt-0.5">
                  <FieldValue field={stated} entity={ops} />
                </dd>
              </div>
            </dl>
            <div>
              <p className="mb-1.5 text-sm font-semibold text-graphite">Most miles</p>
              <ol className="space-y-1">
                {ranked.slice(0, 8).map(([code, m]) => (
                  <li
                    key={code}
                    className="grid grid-cols-[2rem_1fr_4.5rem] items-center gap-2 text-sm"
                  >
                    <span className="font-semibold">{code}</span>
                    <span className="h-1.5 rounded-full bg-surface-sunk">
                      <span
                        className="block h-full rounded-full bg-interstate"
                        style={{ width: `${(m / (ranked[0]?.[1] || 1)) * 100}%` }}
                      />
                    </span>
                    <span className="text-right tabular-nums text-graphite">
                      {((m / (totalMiles || 1)) * 100).toFixed(1)}%
                    </span>
                  </li>
                ))}
              </ol>
            </div>
          </div>
        </div>
      </Panel>

      <IftaTable quarters={ofKind(all, "ifta_quarter")} home={home} />

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
        <Shares
          title="Radius of operation"
          description="Each form uses different distance bands, so they are shown side by side rather than compared."
          lists={summary?.radius ?? []}
        />
        <Shares
          title="Commodities"
          description={
            request && field(request, "cargo_limit")?.value_display
              ? `Load values against the requested cargo limit of ${field(request, "cargo_limit")?.value_display}.`
              : undefined
          }
          lists={summary?.commodities ?? []}
        />
      </div>

      <Answers ops={ops} />
    </div>
  );
}

function Stat({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div>
      <dt className="text-sm text-graphite">{label}</dt>
      <dd className="font-display text-xl font-bold tabular-nums">{value || "None"}</dd>
      {note ? <dd className="text-xs text-graphite">{note}</dd> : null}
    </div>
  );
}

const CROSSFOOT = /^(sum_|row_tax$|summary_total$)/;

function IftaTable({ quarters, home }: { quarters: Entity[]; home: string | null }) {
  const id = useSubmissionId();
  const extractions = useExtractions(id);
  const rows = [...quarters].sort((a, b) => a.key.localeCompare(b.key));
  const series = rows.map((q) => q.summary.ifta?.total_miles ?? 0);
  const checksFor = (q: Entity) =>
    (extractions.data ?? [])
      .find((x) => x.document_id === q.details.document_id)
      ?.checks.filter((c) => CROSSFOOT.test(c.name)) ?? [];
  return (
    <Panel
      title="IFTA returns"
      description="Quarterly fuel tax returns: miles, fuel and anything owed late."
      flush
    >
      <table className="w-full text-base">
        <thead>
          <tr className="border-b border-hairline text-left text-sm text-graphite">
            <th className="px-4 py-2 font-normal">Quarter</th>
            <th className="px-4 py-2 text-right font-normal">Miles</th>
            <th className="px-4 py-2 text-right font-normal">Change</th>
            <th className="px-4 py-2 text-right font-normal">Outside {home ?? "home"}</th>
            <th className="px-4 py-2 text-right font-normal">Taxable gallons</th>
            <th className="px-4 py-2 text-right font-normal">MPG</th>
            <th className="px-4 py-2 text-right font-normal">Penalty and interest</th>
            <th className="px-4 py-2 text-right font-normal">Total due</th>
            <th className="px-4 py-2 font-normal">Cross-foot</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((q) => {
            const s = q.summary.ifta;
            if (!s) return null;
            const change = s.change_pct;
            const late = s.penalty_and_interest;
            return (
              <tr key={q.id} className="border-b border-hairline last:border-0">
                <IftaSource q={q}>
                  {(source) => (
                    <>
                      <td className="px-4 py-2">
                        {source.table ? (
                          <EvidenceLink target={source.table} className="font-semibold">
                            {formatQuarter(q.key)}
                          </EvidenceLink>
                        ) : (
                          <span className="font-semibold">{formatQuarter(q.key)}</span>
                        )}
                      </td>
                      <td className="px-4 py-2 text-right tabular-nums">
                        <span className="inline-flex items-center gap-2">
                          <Sparkline values={series} at={rows.indexOf(q)} />
                          {source.miles ? (
                            <EvidenceLink target={source.miles}>
                              {formatNumber(s.total_miles)}
                            </EvidenceLink>
                          ) : (
                            formatNumber(s.total_miles)
                          )}
                        </span>
                      </td>
                    </>
                  )}
                </IftaSource>
                <td
                  className={`px-4 py-2 text-right tabular-nums ${s.dropped ? "font-semibold text-warning" : "text-graphite"}`}
                >
                  {change == null ? "" : `${change > 0 ? "+" : ""}${change.toFixed(0)}%`}
                </td>
                <td className="px-4 py-2 text-right tabular-nums">
                  {s.outside_home_pct == null ? "" : `${s.outside_home_pct.toFixed(0)}%`}
                </td>
                <td className="px-4 py-2 text-right tabular-nums">
                  {formatNumber(s.taxable_gallons)}
                </td>
                <td className="px-4 py-2 text-right tabular-nums">{s.mpg?.toFixed(2) ?? ""}</td>
                <td
                  className={`px-4 py-2 text-right tabular-nums ${late ? "font-semibold text-warning" : "text-graphite"}`}
                >
                  {late ? (
                    <span
                      className="inline-flex items-center gap-1"
                      title="A penalty or interest means the return or payment was late"
                    >
                      <TriangleAlert size={13} aria-hidden="true" /> Late,{" "}
                      {formatMoney(late.toFixed(2))}
                    </span>
                  ) : (
                    "None"
                  )}
                </td>
                <td className="px-4 py-2 text-right tabular-nums">
                  {s.total_due == null ? "" : formatMoney(s.total_due.toFixed(2))}
                </td>
                <td className="px-4 py-2">
                  <CrossFoot checks={checksFor(q)} q={q} />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </Panel>
  );
}

type IftaTargets = { table: EvidenceTarget | null; miles: EvidenceTarget | null };

/** Where a quarter's figures were read: the jurisdiction table (every state row's box, on its
 * page) for the quarter's name, and the total miles when they were found on the page. A figure
 * links only to a box it was read from; gallons and amounts due are not stored with one. */
function IftaSource({ q, children }: { q: Entity; children: (t: IftaTargets) => React.ReactNode }) {
  const documentId = typeof q.details.document_id === "string" ? q.details.document_id : null;
  const facts = useDocumentFacts(documentId).data ?? [];
  const rows = facts.filter((f) => /^rows\[\d+\]\./.test(f.path) && f.bbox && f.page_id);
  const page = rows[0]?.page_id ?? null;
  const total = facts.find((f) => f.path === "totals.total_miles" && f.bbox);
  const name = formatQuarter(q.key);
  return children({
    table: page
      ? {
          kind: "page",
          pageId: page,
          label: `IFTA ${name}: miles by state`,
          boxes: rows.filter((f) => f.page_id === page).map((f) => f.bbox as number[]),
        }
      : null,
    miles: total
      ? { kind: "facts", factIds: [total.id], label: `IFTA ${name}: total miles` }
      : null,
  });
}

const SHARE_SOURCES = [
  ["application", "Application"],
  ["quote", "Quick quote form"],
] as const;

function Shares({
  title,
  description,
  lists,
}: {
  title: string;
  description?: string;
  lists: Schemas["ShareListOut"][];
}) {
  return (
    <Panel title={title} description={description}>
      <div className="grid grid-cols-2 gap-5">
        {SHARE_SOURCES.map(([key, source]) => {
          const items = lists.find((l) => l.source === key)?.items ?? [];
          return (
            <div key={key}>
              <p className="mb-2 text-sm font-semibold text-graphite">{source}</p>
              {items.length ? (
                <ul className="space-y-2">
                  {items.map((it, i) => {
                    const pct = it.percent ?? 0;
                    const value = it.value;
                    const facts = it.fact_ids;
                    const over = it.above_cargo_limit;
                    return (
                      <li key={i} className="text-sm">
                        <div className="flex justify-between gap-2">
                          {facts.length ? (
                            <EvidenceLink
                              target={{
                                kind: "facts",
                                factIds: facts,
                                label: `${source}: ${it.label}`,
                              }}
                              className="truncate"
                            >
                              {bandLabel(it.label)}
                            </EvidenceLink>
                          ) : (
                            <span className="truncate">{bandLabel(it.label)}</span>
                          )}
                          <span className="shrink-0 font-semibold tabular-nums">{pct}%</span>
                        </div>
                        <span className="mt-1 block h-1.5 rounded-full bg-surface-sunk">
                          <span
                            className="block h-full rounded-full bg-interstate"
                            style={{ width: `${Math.min(pct, 100)}%` }}
                          />
                        </span>
                        {value ? (
                          <span
                            className={`text-xs tabular-nums ${over ? "font-semibold text-warning" : "text-graphite"}`}
                          >
                            Up to {formatMoney(value)}
                            {over ? ", above the cargo limit" : ""}
                          </span>
                        ) : null}
                      </li>
                    );
                  })}
                </ul>
              ) : (
                <p className="text-sm text-graphite">Not stated on this form.</p>
              )}
            </div>
          );
        })}
      </div>
    </Panel>
  );
}

function Answers({ ops }: { ops: Entity | undefined }) {
  if (!ops) return null;
  const order = ops.fields
    .filter((f) => f.attribute !== "farthest_territory")
    .sort(
      (a, b) =>
        Number(b.state === "conflict") - Number(a.state === "conflict") ||
        a.label.localeCompare(b.label),
    );
  const unanswered = ops.summary.operations?.unanswered_questions ?? [];
  return (
    <Panel title="Operations answers" description="What the forms say about how the account runs.">
      <dl className="grid grid-cols-1 gap-x-8 lg:grid-cols-2">
        {order.map((f) => (
          <Row key={f.id} label={f.label} top>
            <FieldValue field={f} entity={ops} />
          </Row>
        ))}
        {unanswered.map((q, i) => (
          <Row key={`u${i}`} top label={<span className="line-clamp-3">{q.question}</span>}>
            <FieldValue field={undefined} empty="Left blank" />
          </Row>
        ))}
      </dl>
    </Panel>
  );
}

/** Miles across the quarters sent, this quarter's point marked. */
function Sparkline({ values, at }: { values: number[]; at: number }) {
  if (values.length < 2) return null;
  const max = Math.max(...values);
  const min = Math.min(...values);
  const W = 56;
  const H = 18;
  const x = (i: number) => (i / (values.length - 1)) * (W - 4) + 2;
  const y = (v: number) => H - 3 - ((v - min) / Math.max(1, max - min)) * (H - 6);
  return (
    <svg width={W} height={H} aria-hidden="true" className="shrink-0">
      <polyline
        points={values.map((v, i) => `${x(i)},${y(v)}`).join(" ")}
        fill="none"
        stroke="var(--graphite)"
        strokeWidth={1.2}
      />
      <circle cx={x(at)} cy={y(values[at])} r={2.5} fill="var(--interstate)" />
    </svg>
  );
}

/** The return's own arithmetic, checked: every column total and the summary. */
function CrossFoot({
  checks,
  q,
}: {
  checks: { name: string; passed: boolean; detail: string }[];
  q: Entity;
}) {
  if (!checks.length) return <span className="text-sm text-graphite">Not checked</span>;
  const failed = checks.filter((c) => !c.passed);
  return (
    <EvidenceLink target={{ kind: "entity", entityIds: [q.id], label: q.label }}>
      {failed.length ? (
        <Chip tone="stop" icon={CircleSlash} title={failed.map((c) => c.detail).join("; ")}>
          {failed.length} of {checks.length} totals off
        </Chip>
      ) : (
        <Chip tone="guide" icon={CircleCheck} title="Every column total and the summary add up">
          Totals check out
        </Chip>
      )}
    </EvidenceLink>
  );
}

/** A form's band as written, tidied: "< 50Miles" -> "< 50 miles", "51 - 200 Miles" -> "51 to 200
 * miles". */
export function bandLabel(label: string): string {
  return label
    .replace(/(\d)\s*Miles?\b/gi, (_m, d: string) => `${d} miles`)
    .replace(/(\d)\s*-\s*(\d)/g, "$1 to $2");
}
