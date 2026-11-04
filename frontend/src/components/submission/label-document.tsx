"use client";

import { BadgeCheck, Check, Tag, Undo2, X } from "lucide-react";
import { toast } from "sonner";
import { useState } from "react";

import { Button } from "@/components/ui/primitives";
import type { Schemas } from "@/lib/api";
import {
  useClearLabel,
  useConfirmDocument,
  useDocumentTypes,
  useLabelDocument,
} from "@/lib/queries";
import { useSession } from "@/lib/session";
import { personName } from "@/lib/format";

/**
 * A person says what a document is when the classifier could not place it or got it wrong. The
 * label is kept, the submission is processed again so the document is read as that type, and
 * both are in the audit trail. Unrecognised documents ask for it; others offer it quietly.
 */
export function LabelDocument({
  submissionId,
  doc,
}: {
  submissionId: string;
  doc: Schemas["DocumentOut"];
}) {
  const session = useSession();
  const types = useDocumentTypes();
  const label = useLabelDocument(submissionId);
  const clear = useClearLabel(submissionId);
  const confirm = useConfirmDocument(submissionId);
  const unplaced = doc.doc_type === "other" || doc.needs_review;
  const [open, setOpen] = useState(false);
  const [docType, setDocType] = useState("");
  const [reason, setReason] = useState("");
  if (!session.can("review") || doc.doc_type === "email_body") return null;
  const name = (v: string) => types.data?.find((t) => t.value === v)?.label ?? v;

  if (doc.label) {
    return (
      <p className="mt-1.5 flex flex-wrap items-center gap-2 text-sm">
        <Tag size={13} className="text-interstate" aria-hidden="true" />
        <span>
          Labelled as <span className="font-semibold">{name(doc.label.doc_type)}</span> by{" "}
          {personName(doc.label.actor)} (the classifier said {name(doc.label.was)})
        </span>
        <Button
          size="sm"
          variant="quiet"
          icon={Undo2}
          busy={clear.isPending}
          onClick={() => clear.mutate(doc.id)}
        >
          Remove label
        </Button>
      </p>
    );
  }
  if (!open) {
    return unplaced ? (
      <div className="mt-2 flex flex-wrap items-center gap-2 rounded-[var(--radius-cell)] border border-dashed border-warning/60 px-3 py-2 text-sm">
        <span className="font-semibold">
          Not recognised with confidence. What is this document?
        </span>
        <Button size="sm" icon={Tag} onClick={() => setOpen(true)}>
          Label it
        </Button>
      </div>
    ) : (
      <span className="mt-1 flex flex-wrap items-center gap-1">
        <Button
          size="sm"
          variant="quiet"
          icon={BadgeCheck}
          busy={confirm.isPending}
          title="Confirm the type; Lane learns this layout so the next one needs no model call"
          onClick={() =>
            confirm.mutate(doc.id, {
              onSuccess: (r) =>
                toast(
                  r.layouts.length
                    ? `Type confirmed. Lane learned ${r.layouts.length === 1 ? "this layout" : `${r.layouts.length} layouts`}.`
                    : "Type confirmed. Too little on the page to learn a layout from.",
                ),
            })
          }
        >
          Confirm type
        </Button>
        <Button size="sm" variant="quiet" icon={Tag} onClick={() => setOpen(true)}>
          Change type
        </Button>
      </span>
    );
  }
  return (
    <form
      className="mt-2 flex flex-wrap items-end gap-2 rounded-[var(--radius-cell)] border border-hairline bg-surface-sunk/60 p-3"
      onSubmit={(e) => {
        e.preventDefault();
        if (!docType || !reason.trim()) return;
        label.mutate({ documentId: doc.id, docType, reason }, { onSuccess: () => setOpen(false) });
      }}
    >
      <label className="text-sm">
        <span className="mb-1 block font-semibold">This document is</span>
        <select
          autoFocus
          value={docType}
          onChange={(e) => setDocType(e.target.value)}
          className="h-8 rounded-[var(--radius-cell)] border border-hairline bg-surface px-2"
        >
          <option value="">Choose a type</option>
          {(types.data ?? [])
            .filter((t) => t.value !== doc.doc_type)
            .map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
        </select>
      </label>
      <label className="min-w-64 flex-1 text-sm">
        <span className="mb-1 block font-semibold">Why</span>
        <input
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="For example: the agent's cover note says it is the MVR"
          className="h-8 w-full rounded-[var(--radius-cell)] border border-hairline bg-surface px-2"
        />
      </label>
      <Button
        type="submit"
        variant="primary"
        size="sm"
        icon={Check}
        busy={label.isPending}
        disabled={!docType || !reason.trim()}
      >
        Label and reprocess
      </Button>
      <Button size="sm" variant="quiet" icon={X} onClick={() => setOpen(false)}>
        Cancel
      </Button>
      {label.error ? (
        <p role="alert" className="w-full text-sm text-stop">
          {label.error.message}
        </p>
      ) : null}
    </form>
  );
}
