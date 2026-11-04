"use client";

import { Check, PencilLine, Undo2, X } from "lucide-react";
import { useState } from "react";

import { useRerun } from "@/components/submission/rerun";
import { Button } from "@/components/ui/primitives";
import type { Schemas } from "@/lib/api";
import { formatDateTime, personName } from "@/lib/format";
import { useClearOverride, useOverrideField } from "@/lib/queries";
import { useSession } from "@/lib/session";

/**
 * Correct a value the documents got wrong or never gave, with a reason. The value is read as
 * the field's kind (a date, an amount, Yes or No), the rules run again and the diff shows. The
 * correction stands until someone undoes it, and both are in the audit trail.
 */
export function CorrectValue({
  submissionId,
  field,
}: {
  submissionId: string;
  field: Schemas["FieldDetail"];
}) {
  const session = useSession();
  const { publish } = useRerun();
  const override = useOverrideField(submissionId);
  const clear = useClearOverride(submissionId);
  const [open, setOpen] = useState(false);
  const [value, setValue] = useState("");
  const [reason, setReason] = useState("");
  const canReview = session.can("review");
  const fix = field.correction;

  if (fix) {
    return (
      <div className="mt-2 rounded-[var(--radius-cell)] border border-interstate/30 bg-interstate-soft/50 px-3 py-2 text-sm">
        <p>
          <span className="font-semibold">Corrected to {fix.display}</span>
          {fix.before ? <span className="text-graphite"> (was {fix.before})</span> : null}
        </p>
        <p className="text-graphite">
          {fix.reason}. {personName(fix.actor)}, {formatDateTime(fix.at)}
        </p>
        {canReview ? (
          <Button
            size="sm"
            variant="quiet"
            icon={Undo2}
            busy={clear.isPending}
            className="mt-1"
            onClick={() =>
              void clear
                .mutateAsync(field.id)
                .then((r) => publish({ ...r, what: `${field.label} correction undone` }))
                .catch(() => undefined)
            }
          >
            Undo correction
          </Button>
        ) : null}
      </div>
    );
  }

  if (!canReview) return null;
  if (!open) {
    return (
      <Button
        size="sm"
        variant="quiet"
        icon={PencilLine}
        className="mt-1"
        onClick={() => setOpen(true)}
      >
        Correct this value
      </Button>
    );
  }
  return (
    <form
      className="mt-2 space-y-2 rounded-[var(--radius-cell)] border border-hairline bg-surface-sunk/60 p-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (!value.trim() || !reason.trim()) return;
        void override
          .mutateAsync({ fieldId: field.id, value, reason })
          .then((r) => {
            setOpen(false);
            setValue("");
            setReason("");
            publish({
              ...r,
              what: `${field.label} corrected`,
              undo: () => clear.mutateAsync(field.id),
              undone: `${field.label} correction undone`,
            });
          })
          .catch(() => undefined);
      }}
    >
      <label className="block">
        <span className="mb-1 block text-sm font-semibold">Correct value</span>
        <input
          autoFocus
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder={`Enter ${field.expected}`}
          aria-describedby="correct-hint"
          className="h-8 w-full rounded-[var(--radius-cell)] border border-hairline bg-surface px-2 text-sm"
        />
        <span id="correct-hint" className="mt-0.5 block text-xs text-graphite">
          Read as {field.expected}.
        </span>
      </label>
      <label className="block">
        <span className="mb-1 block text-sm font-semibold">Why</span>
        <input
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="For example: the agent sent the current registration"
          className="h-8 w-full rounded-[var(--radius-cell)] border border-hairline bg-surface px-2 text-sm"
        />
      </label>
      {override.error ? (
        <p role="alert" className="text-sm text-stop">
          {override.error.message}
        </p>
      ) : null}
      <div className="flex items-center gap-2">
        <Button
          type="submit"
          variant="primary"
          size="sm"
          icon={Check}
          busy={override.isPending}
          disabled={!value.trim() || !reason.trim()}
        >
          Correct and rerun
        </Button>
        <Button size="sm" variant="quiet" icon={X} onClick={() => setOpen(false)}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
