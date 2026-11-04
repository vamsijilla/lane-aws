"use client";

import clsx from "clsx";
import { PencilLine, TriangleAlert } from "lucide-react";

import { EvidenceLink } from "@/components/evidence/evidence-link";
import { Chip, MissingSlot } from "@/components/ui/chips";
import { Identifier } from "@/components/ui/primitives";
import { candidateDisplays, type Entity, type Field } from "@/lib/entities";

const IDENTIFIERS = new Set([
  "vin",
  "license_number",
  "dot_number",
  "mc_number",
  "policy_number",
  "fein",
  "account_number",
  "license_class",
]);

/**
 * A resolved value as underwriters read it: the value, or every candidate when the documents
 * disagree, or an empty slot when nothing states it. Every value opens its evidence.
 */
export function FieldValue({
  field,
  entity,
  className,
  empty,
  quietInvalid = false,
}: {
  field: Field | undefined;
  entity?: Entity;
  className?: string;
  empty?: string;
  /** The reason a value is invalid is shown elsewhere in the row (a VIN check chip). */
  quietInvalid?: boolean;
}) {
  if (!field) return <MissingSlot label={empty} />;
  const label = entity ? `${entity.label}: ${field.label}` : field.label;
  const mono = IDENTIFIERS.has(field.attribute);
  const target = { kind: "field" as const, fieldId: field.id, label };
  // A missing value still opens the lens: that is where a person supplies it.
  if (field.state === "missing")
    return (
      <EvidenceLink target={target} className={className}>
        <MissingSlot label={empty} />
      </EvidenceLink>
    );
  if (field.state === "conflict") {
    const values = candidateDisplays(field);
    return (
      <EvidenceLink
        target={target}
        className={clsx("inline-flex flex-wrap items-baseline gap-x-1.5", className)}
      >
        <TriangleAlert
          size={13}
          className="translate-y-[2px] text-warning"
          aria-label="The documents disagree"
        />
        {/* "or" stays with the value after it, so a narrow cell breaks into clean lines */}
        {values.map((v, i) => (
          <span key={i}>
            {i ? <span className="text-graphite">or </span> : null}
            <span className="font-semibold text-warning">
              {mono ? <Identifier value={v} /> : v}
            </span>
          </span>
        ))}
      </EvidenceLink>
    );
  }
  const value = field.value_display ?? "";
  return (
    <EvidenceLink
      target={target}
      className={clsx(field.state === "invalid" && "text-stop", className)}
    >
      {mono ? <Identifier value={value} /> : value}
      {field.state === "invalid" && !quietInvalid ? (
        <span className="ml-1.5 text-xs font-semibold">{field.invalid[0] ?? "Invalid"}</span>
      ) : null}
      {field.resolution === "reference" ? (
        <span className="ml-1.5 text-xs text-graphite">settled by VIN decode</span>
      ) : null}
      {field.resolution === "override" ? (
        <Chip tone="interstate" icon={PencilLine} className="ml-1.5" title="Corrected by a person">
          Corrected
        </Chip>
      ) : null}
    </EvidenceLink>
  );
}
