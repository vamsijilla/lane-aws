"use client";

import clsx from "clsx";
import { ChevronDown, CircleCheck, CircleSlash, Truck } from "lucide-react";
import { Fragment, useState } from "react";

import { EvidenceLink } from "@/components/evidence/evidence-link";
import { FieldValue } from "@/components/submission/field-value";
import { useSubmissionId } from "@/components/submission/shell";
import { Chip } from "@/components/ui/chips";
import { ErrorNote, Identifier, Panel, Skeleton } from "@/components/ui/primitives";
import { type Entity, field, ofKind } from "@/lib/entities";
import { useEntities } from "@/lib/queries";
import { checkDigit, modelYears, segments } from "@/lib/vin";

export function Vehicles() {
  const id = useSubmissionId();
  const entities = useEntities(id);
  if (entities.isPending) return <Skeleton lines={10} />;
  if (entities.error)
    return <ErrorNote title="Vehicles could not be loaded">{entities.error.message}</ErrorNote>;
  const vehicles = ofKind(entities.data, "vehicle");
  const scheduled = vehicles.filter((v) => v.summary.vehicle?.scheduled);
  const elsewhere = vehicles.filter((v) => !v.summary.vehicle?.scheduled);
  return (
    <div className="space-y-6">
      <p className="text-graphite">
        Every VIN is decoded locally: length, check digit, maker from the first three characters,
        model year from the tenth.
      </p>
      <VehicleTable title="Scheduled units" rows={scheduled} />
      {elsewhere.length ? (
        <VehicleTable
          title="Found only in loss history"
          description="Units in a claim that are not on any vehicle schedule."
          rows={elsewhere}
          unscheduled
        />
      ) : null}
    </div>
  );
}

function VehicleTable({
  title,
  description,
  rows,
  unscheduled,
}: {
  title: string;
  description?: string;
  rows: Entity[];
  unscheduled?: boolean;
}) {
  const [open, setOpen] = useState<Set<string>>(new Set());
  const toggle = (vid: string) =>
    setOpen((o) => {
      const n = new Set(o);
      if (n.has(vid)) n.delete(vid);
      else n.add(vid);
      return n;
    });
  return (
    <Panel title={title} description={description} flush>
      <table className="w-full text-base">
        <thead>
          <tr className="border-b border-hairline text-left text-sm text-graphite">
            <th className="px-4 py-2 font-normal">Unit</th>
            <th className="px-4 py-2 font-normal">VIN</th>
            <th className="px-4 py-2 font-normal">VIN check</th>
            <th className="px-4 py-2 font-normal">Year</th>
            <th className="px-4 py-2 font-normal">Make</th>
            <th className="px-4 py-2 text-right font-normal">Cost new</th>
            <th className="px-4 py-2 text-right font-normal">Stated value</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((v) => {
            const check = v.summary.vehicle?.vin_check;
            const problems = check?.problems ?? [];
            const valid = check?.valid === true;
            return (
              <Fragment key={v.id}>
                <tr
                  id={`vehicle-${v.id}`}
                  className="scroll-mt-40 border-b border-hairline last:border-0"
                >
                  <td className="px-4 py-2.5">
                    <span className="flex items-center gap-2">
                      <Truck
                        size={15}
                        className={unscheduled ? "text-stop" : "text-graphite"}
                        aria-hidden="true"
                      />
                      <EvidenceLink
                        target={{ kind: "entity", entityIds: [v.id], label: v.label }}
                        className="font-semibold"
                      >
                        {v.label}
                      </EvidenceLink>
                    </span>
                  </td>
                  <td className="px-4 py-2.5">
                    <FieldValue field={field(v, "vin")} entity={v} quietInvalid />
                  </td>
                  <td className="px-4 py-2.5">
                    {valid ? (
                      <Chip
                        tone="guide"
                        icon={CircleCheck}
                        title="17 characters, check digit matches"
                      >
                        {(check?.maker ?? "Valid").replace(/^./, (c) => c.toUpperCase())}
                        {check?.model_year ? `, ${check.model_year}` : ""}
                      </Chip>
                    ) : (
                      <Chip tone="stop" icon={CircleSlash}>
                        {problems[0] ?? "Invalid"}
                      </Chip>
                    )}
                    <button
                      type="button"
                      onClick={() => toggle(v.id)}
                      aria-expanded={open.has(v.id)}
                      className="ml-2 inline-flex items-center gap-0.5 text-xs font-semibold text-interstate hover:underline"
                    >
                      <ChevronDown
                        size={13}
                        aria-hidden="true"
                        className={clsx(
                          "transition-transform motion-reduce:transition-none",
                          open.has(v.id) && "rotate-180",
                        )}
                      />
                      Anatomy
                    </button>
                  </td>
                  <td className="px-4 py-2.5 tabular-nums">
                    <FieldValue field={field(v, "year")} entity={v} />
                  </td>
                  <td className="px-4 py-2.5">
                    <FieldValue field={field(v, "make")} entity={v} />
                  </td>
                  <td className="px-4 py-2.5 text-right tabular-nums">
                    <FieldValue field={field(v, "cost_new")} entity={v} empty="Not shown" />
                  </td>
                  <td className="px-4 py-2.5 text-right">
                    <FieldValue field={field(v, "stated_value")} entity={v} />
                  </td>
                </tr>
                {open.has(v.id) ? (
                  <tr className="border-b border-hairline bg-surface-sunk/40">
                    <td colSpan={7} className="px-4 py-3">
                      <VinAnatomy v={v} />
                    </td>
                  </tr>
                ) : null}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </Panel>
  );
}

/** The VIN position by position: what each part means, the check digit worked out. */
function VinAnatomy({ v }: { v: Entity }) {
  const vinField = field(v, "vin");
  const vin = (vinField?.value_display ?? "").replace(/\s/g, "").toUpperCase();
  const check = v.summary.vehicle?.vin_check;
  const make = field(v, "make");
  if (!vin) return <p className="text-sm text-graphite">No VIN was printed for this unit.</p>;
  const short = vin.length < 17;
  const computed = checkDigit(vin);
  const printed = vin[8] ?? "";
  const years = modelYears(vin[9] ?? "");
  const label = `${v.label}: VIN`;
  return (
    <div className="space-y-3 text-sm">
      <ol className="flex flex-wrap gap-2" aria-label="VIN positions">
        {segments(vin).map((s) => (
          <li
            key={s.label}
            className="rounded-[var(--radius-cell)] border border-hairline bg-surface px-2 py-1"
          >
            <span className="block text-xs text-graphite">
              {s.label}, {s.from === s.to ? `position ${s.from}` : `positions ${s.from}-${s.to}`}
            </span>
            <EvidenceLink target={{ kind: "field", fieldId: vinField?.id ?? "", label }}>
              <Identifier value={s.text || " "} className="text-base" />
            </EvidenceLink>
            {short && s.label === "Serial" ? (
              <span
                className="ml-1 inline-block h-5 w-3 rounded-[4px] border-2 border-dashed border-stop align-middle"
                title="One character is missing; which position cannot be told from the VIN"
                aria-label="Missing character"
              />
            ) : null}
          </li>
        ))}
      </ol>
      <dl className="grid grid-cols-[10rem_1fr] gap-x-4 gap-y-1">
        <dt className="text-graphite">Length</dt>
        <dd className={short ? "font-semibold text-stop" : ""}>
          {vin.length} of 17 characters
          {short ? ". One is missing, so the positions after it may be shifted." : ""}
        </dd>
        <dt className="text-graphite">Check digit</dt>
        <dd>
          {computed === null ? (
            <span className="text-graphite">
              Cannot be worked out: the VIN is not 17 characters
            </span>
          ) : computed === printed ? (
            <span className="text-guide">
              Printed {printed}, computed {computed}: matches
            </span>
          ) : (
            <span className="font-semibold text-stop">
              Printed {printed}, computed {computed}: does not match
            </span>
          )}
        </dd>
        <dt className="text-graphite">Maker (positions 1-3)</dt>
        <dd>
          {check?.maker ?? "Not decoded"}
          {make?.state === "conflict" ? (
            <EvidenceLink
              target={{ kind: "field", fieldId: make.id, label: `${v.label}: Make` }}
              className="ml-2 font-semibold text-warning"
            >
              The documents disagree on the make
            </EvidenceLink>
          ) : null}
        </dd>
        <dt className="text-graphite">Model year (position 10)</dt>
        <dd>
          {years.length ? years.join(" or ") : "Not decoded"}
          {check?.model_year ? `; read as ${check.model_year}` : ""}
        </dd>
      </dl>
    </div>
  );
}
