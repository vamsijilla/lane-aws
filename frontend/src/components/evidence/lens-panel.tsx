"use client";

import clsx from "clsx";
import { ArrowRight, X } from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { ConflictStrip } from "@/components/evidence/conflict-strip";
import { CorrectValue } from "@/components/evidence/correct-value";
import { ItemView, PlainPage, RevealButton } from "@/components/evidence/item-view";
import { type EvidenceTarget, type SingleTarget, useLens } from "@/components/evidence/lens";
import { factItems, fieldItems, type Item } from "@/components/evidence/lens-items";
import { SourceSwitcher } from "@/components/evidence/source-switcher";
import { refTarget } from "@/components/evidence/targets";
import { MissingSlot, StateChip } from "@/components/ui/chips";
import { Kbd, Skeleton } from "@/components/ui/primitives";
import { useEntities, useFacts, useField } from "@/lib/queries";

export function LensPanel({
  submissionId,
  target,
  onClose,
}: {
  submissionId: string;
  target: EvidenceTarget | null;
  onClose: () => void;
}) {
  const reduce = useReducedMotion();
  return (
    <AnimatePresence>
      {target ? (
        <motion.aside
          key="lens"
          role="dialog"
          aria-modal="false"
          aria-label="Evidence"
          initial={reduce ? { opacity: 0 } : { x: 48, opacity: 0 }}
          animate={reduce ? { opacity: 1 } : { x: 0, opacity: 1 }}
          exit={reduce ? { opacity: 0 } : { x: 48, opacity: 0 }}
          transition={{ duration: 0.18, ease: [0.2, 0.8, 0.2, 1] }}
          className="fixed top-14 right-0 bottom-0 z-40 flex w-[min(46vw,860px)] min-w-[520px] flex-col border-l border-hairline bg-surface shadow-[var(--shadow-overlay)]"
        >
          {target.kind === "refs" ? (
            <RunBody
              key={JSON.stringify(target)}
              submissionId={submissionId}
              target={target}
              onClose={onClose}
            />
          ) : (
            <LensBody
              key={JSON.stringify(target)}
              submissionId={submissionId}
              target={target}
              onClose={onClose}
            />
          )}
        </motion.aside>
      ) : null}
    </AnimatePresence>
  );
}

type RunTarget = Extract<EvidenceTarget, { kind: "refs" }>;

/** A run of footnotes: pick one, and it opens by what it cites. */
function RunBody({
  submissionId,
  target,
  onClose,
}: {
  submissionId: string;
  target: RunTarget;
  onClose: () => void;
}) {
  const [at, setAt] = useState(0);
  const note = target.notes[Math.min(at, target.notes.length - 1)];
  const inner: SingleTarget = refTarget(note, `Source ${note.n}`, target.refs) ?? {
    kind: "facts",
    factIds: [],
    label: note.label || `Source ${note.n}`,
  };
  const chooser = (
    <div className="border-b border-hairline px-5 py-2.5">
      <p className="text-xs text-graphite">{target.label}</p>
      <div className="mt-1 flex flex-wrap gap-1.5" role="group" aria-label={target.label}>
        {target.notes.map((f, i) => (
          <button
            key={f.n}
            type="button"
            aria-pressed={i === at}
            onClick={() => setAt(i)}
            className={clsx(
              "max-w-full rounded-[var(--radius-cell)] border px-1.5 py-0.5 text-left text-xs [overflow-wrap:anywhere]",
              i === at
                ? "border-interstate bg-interstate-soft text-interstate"
                : "border-hairline bg-surface-sunk hover:border-interstate hover:text-interstate",
            )}
          >
            <span className="mr-1 font-mono font-semibold">{f.n}</span>
            {f.label}
          </button>
        ))}
      </div>
    </div>
  );
  return (
    <LensBody
      key={at}
      submissionId={submissionId}
      target={inner}
      onClose={onClose}
      chooser={chooser}
    />
  );
}

function LensBody({
  submissionId,
  target,
  onClose,
  chooser,
}: {
  submissionId: string;
  target: SingleTarget;
  onClose: () => void;
  chooser?: React.ReactNode;
}) {
  const lens = useLens();
  const field = useField(target.kind === "field" ? target.fieldId : null);
  const entities = useEntities(submissionId);
  const entityFactIds = useMemo(() => {
    if (target.kind !== "entity") return [];
    const ids = new Set<string>();
    for (const e of entities.data ?? []) {
      if (!target.entityIds.includes(e.id)) continue;
      for (const f of e.fields)
        for (const c of f.candidates)
          for (const src of c.sources) for (const id of src.fact_ids) ids.add(id);
    }
    return [...ids].slice(0, 60);
  }, [entities.data, target]);
  const facts = useFacts(
    target.kind === "facts"
      ? target.factIds.slice(0, 60)
      : target.kind === "entity"
        ? entityFactIds
        : [],
  );

  const items: Item[] = useMemo(
    () => (target.kind === "field" ? fieldItems(field.data) : factItems(facts.data)),
    [target, field.data, facts.data],
  );

  const [index, setIndex] = useState(0);
  const count = items.length;
  const current = items[Math.min(index, Math.max(count - 1, 0))];

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.target instanceof HTMLElement && e.target.closest("input, textarea, select")) return;
      if (e.key === "ArrowRight") setIndex((i) => Math.min(i + 1, count - 1));
      if (e.key === "ArrowLeft") setIndex((i) => Math.max(i - 1, 0));
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [count]);

  const loading =
    target.kind === "field" ? field.isPending : facts.isPending && facts.fetchStatus !== "idle";
  const title =
    target.kind === "field"
      ? field.data
        ? `${field.data.entity_label}: ${field.data.label}`
        : (target.label ?? "Evidence")
      : target.label;

  return (
    <>
      {chooser}
      <header className="flex items-start gap-3 border-b border-hairline px-5 py-3.5">
        <div className="min-w-0 flex-1">
          <p className="text-sm text-graphite">Evidence</p>
          <h2 className="truncate font-serif text-xl font-medium" title={title}>
            {title}
          </h2>
          {field.data && target.kind === "field" ? (
            <div className="mt-1.5 flex flex-wrap items-center gap-2">
              <StateChip state={field.data.state} count={field.data.candidates.length} />
              {field.data.state !== "missing" && field.data.state !== "conflict" ? (
                <span className="font-semibold">{field.data.value_display}</span>
              ) : null}
              {field.data.personal_data ? <RevealButton fieldId={field.data.id} /> : null}
            </div>
          ) : null}
          {field.data && target.kind === "field" ? (
            <CorrectValue submissionId={submissionId} field={field.data} />
          ) : null}
          {(target.kind === "facts" || target.kind === "page") && target.note ? (
            <p className="mt-1 text-sm text-graphite">{target.note}</p>
          ) : null}
          {target.kind === "facts" && target.inputs?.length ? (
            <div className="mt-1.5 flex flex-wrap items-baseline gap-1.5 text-xs">
              <span className="text-graphite">From</span>
              {target.inputs.map((x) => (
                <button
                  key={x.label}
                  type="button"
                  onClick={() => lens.open(x.target)}
                  className="max-w-full rounded-[var(--radius-cell)] border border-hairline bg-surface-sunk px-1.5 py-0.5 text-left [overflow-wrap:anywhere] hover:border-interstate hover:text-interstate"
                >
                  {x.label}
                </button>
              ))}
            </div>
          ) : null}
          {target.kind === "facts" && target.panel ? (
            <Link
              href={`/submissions/${submissionId}/${target.panel.tab}${target.panel.anchor ? `#${target.panel.anchor}` : ""}`}
              onClick={onClose}
              className="mt-1.5 inline-flex items-center gap-1 text-sm text-interstate hover:underline"
            >
              {target.panel.label}
              <ArrowRight size={14} aria-hidden="true" />
            </Link>
          ) : null}
        </div>
        <button
          type="button"
          onClick={onClose}
          className="rounded-[var(--radius-cell)] p-1.5 text-graphite hover:bg-surface-sunk hover:text-ink"
          aria-label="Close evidence (Esc)"
        >
          <X size={18} />
        </button>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
        {target.kind === "page" ? (
          <PlainPage
            submissionId={submissionId}
            pageId={target.pageId}
            label={target.label}
            boxes={target.boxes}
          />
        ) : loading ? (
          <Skeleton lines={8} />
        ) : field.error ? (
          <p className="text-sm text-graphite">
            This value is no longer in the submission. It may have changed since this was written.
          </p>
        ) : count === 0 ? (
          <div className="space-y-3 text-sm text-graphite">
            {field.data?.state === "missing" ? <MissingSlot /> : null}
            <p>{(target.kind === "facts" && target.empty) || "No document states this value."}</p>
            {field.data?.notes.map((n) => (
              <p key={n}>{n}</p>
            ))}
          </div>
        ) : (
          <>
            {field.data?.conflict ? (
              <ConflictStrip
                submissionId={submissionId}
                field={field.data}
                active={current?.candidate?.index}
                onPick={(ci) => setIndex(items.findIndex((x) => x.candidate?.index === ci))}
              />
            ) : null}
            <SourceSwitcher
              submissionId={submissionId}
              items={items}
              index={index}
              onChange={setIndex}
            />
            {current ? <ItemView submissionId={submissionId} item={current} /> : null}
          </>
        )}
      </div>
      <footer className="flex items-center gap-3 border-t border-hairline px-5 py-2 text-xs text-graphite">
        <span className="inline-flex items-center gap-1">
          <Kbd>←</Kbd>
          <Kbd>→</Kbd> sources
        </span>
        <span className="inline-flex items-center gap-1">
          <Kbd>Esc</Kbd> close
        </span>
      </footer>
    </>
  );
}
