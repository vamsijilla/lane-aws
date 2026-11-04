"use client";

import { useQuery } from "@tanstack/react-query";
import clsx from "clsx";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import Link from "next/link";
import { useState } from "react";

import { EvidenceProvider } from "@/components/evidence/lens";
import { EvidenceLink } from "@/components/evidence/evidence-link";
import { RerunProvider } from "@/components/submission/rerun";
import { ErrorNote, Skeleton } from "@/components/ui/primitives";
import { listSubmissions, type Schemas } from "@/lib/api";
import { useHow } from "@/lib/queries";

type How = Schemas["HowOut"];
type Stage = Schemas["StageOut"];

const TAB_NAMES: Record<string, string> = {
  "": "Brief",
  "/documents": "Documents",
  "/drivers": "Drivers",
  "/conflicts": "Conflicts",
  "/rules": "Rules",
  "/findings": "AI findings",
  "/actions": "Next actions",
  "/audit": "Audit",
};

const AI_WORK = [
  "Typing pages that fixed signals cannot place",
  "Reading each document into catalog fields",
  "A stronger second read when the first one fails its checks",
  "The expert review, after an underwriter approves its cost",
  "Wording the email to the agent; every detail in it is inserted by code",
];

const CODE_WORK = [
  "Decoding every VIN",
  "Validators on each value, and recall checks against printed counts and totals",
  "Every guideline rule, and the route to a team",
  "Masking driver identifiers",
  "The audit chain, where each event's hash covers the one before",
];

const PRODUCTION = [
  ["Email intake", "Mailpit", "Amazon SES or Microsoft Graph"],
  ["Database", "Postgres", "Amazon RDS for Postgres"],
  ["Files", "MinIO", "Amazon S3, encrypted with KMS"],
  ["Workers", "Worker leases in Postgres", "Fargate tasks reading from SQS"],
  ["Models", "Anthropic API", "Claude on Amazon Bedrock"],
  ["OCR", "RapidOCR", "Amazon Textract or Azure Document Intelligence"],
];

export function HowLaneWorks() {
  const subs = useQuery({
    queryKey: ["submissions"],
    queryFn: ({ signal }) => listSubmissions(signal),
  });
  const [picked, setPicked] = useState<string | null>(null);
  const roots = (subs.data ?? []).filter((s) => !s.relation || s.relation === "kept_separate");
  const id = picked ?? roots[0]?.id ?? null;
  const how = useHow(id);

  return (
    <div className="mx-auto max-w-[1180px] px-8 py-8">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="max-w-[70ch]">
          <h1 className="font-serif text-2xl font-medium">How Lane works</h1>
          <p className="mt-1 text-graphite">
            The path one submission takes from the agent&apos;s email to a decision, with the
            numbers from its latest run.
          </p>
        </div>
        {roots.length ? (
          <label className="text-sm">
            <span className="mr-2 text-graphite">Submission</span>
            <select
              value={id ?? ""}
              onChange={(e) => setPicked(e.target.value)}
              className="rounded-[var(--radius-control)] border border-hairline bg-surface px-2 py-1"
            >
              {roots.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.applicant ?? s.subject}
                </option>
              ))}
            </select>
          </label>
        ) : null}
      </header>

      {subs.isPending || (id && how.isPending) ? (
        <div className="mt-8">
          <Skeleton lines={12} />
        </div>
      ) : !id ? (
        <p className="mt-8 text-graphite">No submission yet. Upload an email from the Inbox.</p>
      ) : how.error ? (
        <div className="mt-8">
          <ErrorNote title="This page could not be loaded">{how.error.message}</ErrorNote>
        </div>
      ) : how.data ? (
        <RerunProvider>
          <EvidenceProvider submissionId={id}>
            <Body how={how.data} />
          </EvidenceProvider>
        </RerunProvider>
      ) : null}
    </div>
  );
}

function Body({ how }: { how: How }) {
  const base = `/submissions/${how.submission_id}`;
  return (
    <>
      <Road how={how} />

      <Section title="Where AI works and where code decides">
        <div className="grid max-w-[920px] grid-cols-1 gap-8 md:grid-cols-2">
          <div>
            <h3 className="font-semibold">A model reads</h3>
            <ul className="mt-2 list-disc space-y-1 pl-5">
              {AI_WORK.map((t) => (
                <li key={t}>{t}</li>
              ))}
            </ul>
          </div>
          <div>
            <h3 className="font-semibold">Code decides</h3>
            <ul className="mt-2 list-disc space-y-1 pl-5">
              {CODE_WORK.map((t) => (
                <li key={t}>{t}</li>
              ))}
            </ul>
          </div>
        </div>
        <p className="mt-5 max-w-[70ch] font-semibold">
          AI can make a submission more careful, never less.
        </p>
      </Section>

      <Section title="Guarantees, with this submission's proof">
        <dl className="grid max-w-[980px] grid-cols-1 gap-x-10 gap-y-4 md:grid-cols-[minmax(0,18rem)_minmax(0,1fr)]">
          {how.guarantees.map((g) => (
            <div key={g.claim} className="contents">
              <dt className="font-semibold">{g.claim}</dt>
              <dd className="max-w-[70ch] text-graphite">
                {g.field_id ? (
                  <EvidenceLink target={{ kind: "field", fieldId: g.field_id, label: g.claim }}>
                    {g.proof}
                  </EvidenceLink>
                ) : g.link ? (
                  <Link
                    href={`${base}${g.link}`}
                    className="underline underline-offset-2 hover:text-interstate"
                  >
                    {g.proof}
                  </Link>
                ) : (
                  g.proof
                )}
              </dd>
            </div>
          ))}
        </dl>
      </Section>

      <Section title="Where a person decides">
        <ol className="max-w-[70ch] space-y-2">
          {how.person.map((p, i) => (
            <li
              key={p.step}
              className="grid grid-cols-[2rem_minmax(0,1fr)_auto] items-baseline gap-3"
            >
              <span className="text-graphite tabular-nums">{i + 1}.</span>
              <Link href={`${base}${p.tab}`} className="hover:text-interstate">
                {p.step}
              </Link>
              <span className="tabular-nums text-graphite">
                {p.count === 0 ? "not yet" : `${p.count} ${p.count === 1 ? "time" : "times"}`}
              </span>
            </li>
          ))}
        </ol>
        <p className="mt-3 max-w-[70ch] text-sm text-graphite">Counted from the audit trail.</p>
      </Section>

      <Section title="Proof">
        <dl className="flex max-w-[1000px] flex-wrap gap-x-10 gap-y-3">
          {how.proof.map((f) => (
            <div key={f.label}>
              <dt className="text-sm text-graphite">{f.label}</dt>
              <dd
                className={
                  /^\d/.test(f.value)
                    ? "font-[family-name:var(--font-overpass)] text-xl font-bold"
                    : "max-w-[34ch] pt-1 font-semibold"
                }
              >
                {f.value}
              </dd>
            </div>
          ))}
        </dl>
        <p className="mt-3 max-w-[70ch] text-sm text-graphite">
          The gold set was written by hand by the person who built Lane, from the packet it was
          developed on. The perturbations and the unseen packet are the stronger evidence. The full
          reports are on{" "}
          <Link href="/evals" className="underline underline-offset-2 hover:text-interstate">
            Evals
          </Link>
          .
        </p>
      </Section>

      <Section title="In production">
        <table className="w-full max-w-[860px] text-left text-sm">
          <thead>
            <tr className="border-b border-hairline text-graphite">
              <th className="py-1.5 pr-6 font-normal">Part</th>
              <th className="py-1.5 pr-6 font-normal">Here</th>
              <th className="py-1.5 font-normal">In production</th>
            </tr>
          </thead>
          <tbody>
            {PRODUCTION.map(([part, here, prod]) => (
              <tr key={part} className="border-b border-hairline last:border-0">
                <td className="py-1.5 pr-6">{part}</td>
                <td className="py-1.5 pr-6 text-graphite">{here}</td>
                <td className="py-1.5">{prod}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="mt-3 max-w-[70ch]">
          Workers take jobs by lease, so one that stops is replaced without losing work; pages run
          in parallel; and every reading is cached by content, so the same file is never read twice.
        </p>
      </Section>
    </>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mt-14">
      <h2 className="font-display text-lg font-bold">{title}</h2>
      <div className="mt-4">{children}</div>
    </section>
  );
}

/** The lane: eight mile markers on one road, drawn once on first load. */
function Road({ how }: { how: How }) {
  const reduce = useReducedMotion();
  const [open, setOpen] = useState<string | null>(null);
  const stage = how.stages.find((s) => s.key === open) ?? null;
  const n = how.stages.length;
  return (
    <section className="mt-10" aria-label="The lane, stage by stage">
      <div className="relative overflow-x-auto pb-2">
        <div className="relative min-w-[960px]">
          <svg
            className="absolute top-[34px] left-0 h-3 w-full"
            viewBox="0 0 1000 12"
            preserveAspectRatio="none"
            aria-hidden="true"
          >
            <motion.line
              x1="0"
              y1="6"
              x2="1000"
              y2="6"
              className="stroke-ink"
              strokeWidth="10"
              initial={reduce ? false : { pathLength: 0 }}
              animate={{ pathLength: 1 }}
              transition={{ duration: 0.9, ease: "easeOut" }}
            />
            <motion.line
              x1="0"
              y1="6"
              x2="1000"
              y2="6"
              stroke="white"
              strokeWidth="1.5"
              strokeDasharray="14 12"
              initial={reduce ? false : { opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ delay: reduce ? 0 : 0.9, duration: 0.2 }}
            />
          </svg>
          <ol
            className="relative grid"
            style={{ gridTemplateColumns: `repeat(${n}, minmax(0, 1fr))` }}
          >
            {how.stages.map((s, i) => (
              <motion.li
                key={s.key}
                initial={reduce ? false : { opacity: 0, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: reduce ? 0 : 0.25 + (i / n) * 0.7, duration: 0.25 }}
                className="flex flex-col items-center px-1 text-center"
              >
                <button
                  type="button"
                  onClick={() => setOpen(open === s.key ? null : s.key)}
                  aria-expanded={open === s.key}
                  aria-controls="stage-panel"
                  className="group flex flex-col items-center"
                >
                  <MileMarker n={i + 1} active={open === s.key} />
                  <span className="mt-2 font-semibold group-hover:text-interstate">{s.name}</span>
                </button>
                <span className="mt-1 font-[family-name:var(--font-overpass)] text-2xl font-bold tabular-nums">
                  {s.count ?? s.headline ?? " "}
                </span>
                <span className="mt-0.5 max-w-[16ch] text-sm break-words text-graphite">
                  {s.detail}
                </span>
                <span className="mt-1.5 text-xs text-graphite">{s.by.join(" and ")}</span>
              </motion.li>
            ))}
          </ol>
        </div>
      </div>
      <AnimatePresence initial={false}>
        {stage ? (
          <motion.div
            key={stage.key}
            id="stage-panel"
            initial={reduce ? false : { opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={reduce ? undefined : { opacity: 0, height: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden"
          >
            <StagePanel stage={stage} base={`/submissions/${how.submission_id}`} />
          </motion.div>
        ) : null}
      </AnimatePresence>
    </section>
  );
}

function MileMarker({ n, active }: { n: number; active: boolean }) {
  return (
    <span
      className={clsx(
        "relative z-10 flex h-[60px] w-11 items-center justify-center rounded-[5px] bg-sign-guide p-[3px]",
        active && "ring-2 ring-interstate ring-offset-2 ring-offset-surface",
      )}
    >
      <span className="flex h-full w-full items-center justify-center rounded-[3px] border-2 border-white font-[family-name:var(--font-overpass)] text-2xl leading-none font-bold text-white">
        {n}
      </span>
    </span>
  );
}

function StagePanel({ stage, base }: { stage: Stage; base: string }) {
  const rows: [string, string][] = [
    ["What happens", stage.what],
    ["Method", stage.method],
    ["Model", stage.model ?? "None; code only"],
    [
      "This run",
      [
        stage.seconds != null ? `${stage.seconds} s` : "at intake",
        stage.cost_usd != null ? `$${stage.cost_usd.toFixed(2)} in model calls` : null,
      ]
        .filter(Boolean)
        .join(", "),
    ],
    ["Guarantee", stage.guarantee],
  ];
  return (
    <div className="mt-4 rounded-[var(--radius-panel)] border border-hairline px-5 py-4">
      <h3 className="font-display text-md font-bold">{stage.name}</h3>
      <dl className="mt-2 grid max-w-[900px] grid-cols-[9rem_minmax(0,1fr)] gap-x-6 gap-y-1.5 text-sm">
        {rows.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-graphite">{k}</dt>
            <dd className="max-w-[70ch]">{v}</dd>
          </div>
        ))}
      </dl>
      <Link
        href={`${base}${stage.tab}`}
        className="mt-3 inline-block text-sm font-semibold text-interstate underline-offset-2 hover:underline"
      >
        Open {TAB_NAMES[stage.tab] ?? "the submission"}
      </Link>
    </div>
  );
}
