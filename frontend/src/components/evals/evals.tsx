"use client";

import clsx from "clsx";
import { Check, CircleCheck, CircleX, FlaskConical, Gauge } from "lucide-react";
import Link from "next/link";

import { Empty, ErrorNote, PageTitle, Panel, Skeleton } from "@/components/ui/primitives";
import type { Schemas } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { useEvals, usePerturbations } from "@/lib/queries";

/** Gold-set accuracy: every metric with its score and every miss in plain words. */
export function Evals() {
  const evals = useEvals();
  return (
    <div className="mx-auto max-w-6xl px-8 py-6">
      <PageTitle
        title="Evals"
        description="How well Lane reads, reconciles and decides, measured three ways."
      />
      <p className="mb-6 max-w-[80ch] rounded-[var(--radius-panel)] border border-hairline px-4 py-3 text-sm text-graphite">
        Read the gold-set scores with care: the gold set was written by hand from the sample&apos;s
        source documents by the person who built Lane, on the packet Lane was developed against. The
        evidence that Lane generalizes is the perturbation suite and the synthetic packet: packets
        it was not tuned on, each with its expected effect written before it ran.
      </p>
      <Perturbations />
      {evals.isPending ? (
        <Skeleton lines={10} />
      ) : evals.error ? (
        <ErrorNote title="Eval reports could not be loaded">{evals.error.message}</ErrorNote>
      ) : evals.data.length === 0 ? (
        <Empty icon={Gauge} title="No eval reports yet">
          Run <code className="font-mono">make eval</code> after processing the sample submission.
        </Empty>
      ) : (
        <div className="space-y-8">
          {evals.data.map((r) => (
            <Report key={r.gold} r={r} />
          ))}
        </div>
      )}
    </div>
  );
}

function tone(rate: number | null | undefined) {
  if (rate === null || rate === undefined) return "text-graphite";
  if (rate >= 0.95) return "text-guide";
  if (rate >= 0.8) return "text-warning";
  return "text-stop";
}

function Report({ r }: { r: Schemas["EvalReportOut"] }) {
  const figures = r.figures;
  const cost = Object.values(figures.cost_usd_by_purpose).reduce((a, b) => a + b, 0);
  const misses = r.metrics.flatMap((m) => m.misses.map((x) => [m.name, x] as const));
  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 className="font-display text-lg font-bold">
          Gold set <span className="font-mono">{r.gold}</span>
        </h2>
        <p className="text-sm text-graphite">
          Scored {formatDateTime(r.generated_at)} on{" "}
          <Link
            href={`/submissions/${r.submission_id}`}
            className="text-interstate underline underline-offset-2"
          >
            the submission
          </Link>
        </p>
      </div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5">
        {r.metrics.map((m) => (
          <div key={m.name} className="rounded-[var(--radius-panel)] border border-hairline p-3">
            <p className="text-sm text-graphite">{m.name}</p>
            <p className={clsx("font-display text-2xl font-bold tabular-nums", tone(m.rate))}>
              {m.rate === null || m.rate === undefined ? "n/a" : `${Math.round(m.rate * 100)}%`}
            </p>
            <p className="text-xs tabular-nums text-graphite">
              {m.correct} of {m.total}
            </p>
            <span className="mt-2 block h-1 rounded-full bg-surface-sunk">
              <span
                className={clsx(
                  "block h-full rounded-full",
                  (m.rate ?? 0) >= 0.95
                    ? "bg-guide"
                    : (m.rate ?? 0) >= 0.8
                      ? "bg-warning"
                      : "bg-stop",
                )}
                style={{ width: `${(m.rate ?? 0) * 100}%` }}
              />
            </span>
          </div>
        ))}
      </div>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_20rem]">
        <Panel
          title="Misses"
          description="Everything the system got wrong or did not find, against the gold set."
          flush
        >
          {misses.length ? (
            <ul>
              {misses.map(([metric, miss], i) => (
                <li
                  key={i}
                  className="grid grid-cols-[12rem_1fr] gap-3 border-b border-hairline px-4 py-2 text-sm last:border-0"
                >
                  <span className="text-graphite">{metric}</span>
                  <span>{miss}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="px-4 py-4 text-guide">None.</p>
          )}
        </Panel>
        <Panel title="Figures">
          <dl className="space-y-2 text-sm">
            <div className="flex justify-between">
              <dt className="text-graphite">Known layouts</dt>
              <dd>
                {figures.layout_memory
                  ? "On for this run"
                  : "Off: pages typed by tier 1 signals and the model"}
              </dd>
            </div>
            {figures.pages_classified ? (
              <div className="grid grid-cols-[1fr_auto] gap-x-3">
                <dt className="text-graphite">Pages typed by tier 1 signals</dt>
                <dd className="tabular-nums">
                  {figures.pages_by_tier1} of {figures.pages_classified}
                </dd>
                <dd className="col-span-2 text-xs text-graphite">
                  Form identifiers, worksheet headers, strong key phrases, lookup sheets.
                </dd>
              </div>
            ) : null}
            {figures.pages_classified ? (
              <div className="grid grid-cols-[1fr_auto] gap-x-3">
                <dt className="text-graphite">Pages typed by a known layout</dt>
                <dd className="tabular-nums">
                  {figures.pages_by_layout} of {figures.pages_classified}
                </dd>
                <dd className="col-span-2 text-xs text-graphite">
                  No model call for these.{" "}
                  {figures.layout_audited
                    ? `The model audit agreed on ${figures.layout_audit_agreed} of ${figures.layout_audited}.`
                    : "Not audited in this run."}
                </dd>
              </div>
            ) : null}
            <div className="flex justify-between">
              <dt className="text-graphite">Facts extracted</dt>
              <dd className="tabular-nums">{figures.facts?.toLocaleString("en-US")}</dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-graphite">Located on the source</dt>
              <dd className="tabular-nums">
                {figures.grounding_rate !== null && figures.grounding_rate !== undefined
                  ? `${(figures.grounding_rate * 100).toFixed(1)}%`
                  : ""}
              </dd>
            </div>
            <div className="grid grid-cols-[1fr_auto] gap-x-3">
              <dt className="text-graphite">Findings</dt>
              <dd className="tabular-nums">{figures.findings}</dd>
              <dd className="col-span-2 text-xs text-graphite">
                {Object.entries(figures.findings_by_kind ?? {})
                  .map(([k, v]) => `${v} ${k}`)
                  .join(", ")}
              </dd>
            </div>
            <div className="flex justify-between">
              <dt className="text-graphite">Model cost</dt>
              <dd className="tabular-nums">${cost.toFixed(2)}</dd>
            </div>
          </dl>
        </Panel>
      </div>
    </section>
  );
}

function Perturbations() {
  const report = usePerturbations();
  if (report.isPending) return <Skeleton lines={4} />;
  if (report.error) {
    return (
      <Empty icon={FlaskConical} title="No perturbation report yet">
        Run <code className="font-mono">make perturb</code>.
      </Empty>
    );
  }
  const results = report.data.results;
  const passed = results.filter((r) => r.passed).length;
  return (
    <section className="mb-10 space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 className="font-display text-lg font-bold">
          Perturbations and a packet Lane has never seen
        </h2>
        <p className="text-sm text-graphite">
          {passed} of {results.length} pass, run {formatDateTime(report.data.generated_at)}
        </p>
      </div>
      <Matrix results={results} />
      <Panel flush>
        <ul>
          {results.map((r) => (
            <li
              key={r.name}
              className="grid grid-cols-[13rem_minmax(0,1fr)] gap-4 border-b border-hairline px-4 py-3 last:border-0"
            >
              <div>
                <p className="flex items-center gap-1.5 font-semibold">
                  {r.passed ? (
                    <CircleCheck size={16} className="text-guide" aria-label="Pass" />
                  ) : (
                    <CircleX size={16} className="text-stop" aria-label="Fail" />
                  )}
                  <Link href={`/submissions/${r.submission_id}`} className="hover:text-interstate">
                    {perturbationName(r.name)}
                  </Link>
                </p>
                <p className="mt-0.5 text-xs text-graphite">{r.headline}</p>
              </div>
              <div className="min-w-0">
                <p className="text-sm">{r.what}</p>
                {/* Passing checks are quiet text with a tick; a failure is the one thing that colours */}
                <ul className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1">
                  {r.checks.map((c) => (
                    <li
                      key={c.check}
                      title={c.detail}
                      className={clsx(
                        "inline-flex items-center gap-1 text-xs",
                        c.ok ? "text-graphite" : "font-semibold text-stop",
                      )}
                    >
                      {c.ok ? (
                        <Check
                          size={12}
                          strokeWidth={2.6}
                          className="text-guide"
                          aria-hidden="true"
                        />
                      ) : (
                        "Failed: "
                      )}
                      {c.check}
                    </li>
                  ))}
                </ul>
                {r.checks
                  .filter((c) => !c.ok)
                  .map((c) => (
                    <p key={c.check} className="mt-1 text-xs text-stop">
                      {c.check}: {c.detail}
                    </p>
                  ))}
              </div>
            </li>
          ))}
        </ul>
      </Panel>
    </section>
  );
}

type Result = NonNullable<ReturnType<typeof usePerturbations>["data"]>["results"][number];

const COLUMNS: [string, RegExp][] = [
  ["Route", /recommendation and route|^route\b/i],
  ["Other rules", /rule outcomes|rules reading|every other rule|^only /i],
  ["Documents", /\bdocuments\b/i],
  ["Run completes", /run completes/i],
  ["Case checks", /.*/],
];

function column(check: string): string {
  return COLUMNS.find(([, re]) => re.test(check))?.[0] ?? "Case checks";
}

/** Every perturbation against the same questions; a cell lists its checks on hover. */
function Matrix({ results }: { results: Result[] }) {
  return (
    <Panel flush>
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-hairline text-left text-graphite">
            <th className="px-4 py-2 font-normal">Perturbation</th>
            {COLUMNS.map(([name]) => (
              <th key={name} className="px-3 py-2 font-normal">
                {name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {results.map((r) => (
            <tr key={r.name} className="border-b border-hairline last:border-0">
              <td className="px-4 py-1.5 font-semibold">{perturbationName(r.name)}</td>
              {COLUMNS.map(([name]) => {
                const checks = r.checks.filter((c) => column(c.check) === name);
                if (!checks.length)
                  return (
                    <td key={name} className="px-3 py-1.5 text-graphite">
                      Not checked
                    </td>
                  );
                const ok = checks.every((c) => c.ok);
                const tip = checks
                  .map((c) => `${c.ok ? "Pass" : "Fail"}: ${c.check} (${c.detail})`)
                  .join("\n");
                return (
                  <td key={name} className="px-3 py-1.5">
                    <Link
                      href={`/submissions/${r.submission_id}`}
                      title={tip}
                      aria-label={`${r.name}, ${name}: ${ok ? "pass" : "fail"}. ${tip}`}
                      className={clsx(
                        "inline-flex items-center gap-1 text-sm",
                        ok ? "text-guide" : "font-semibold text-stop",
                      )}
                    >
                      {ok ? (
                        <CircleCheck size={12} aria-hidden="true" />
                      ) : (
                        <CircleX size={12} aria-hidden="true" />
                      )}
                      {ok ? "Pass" : "Fail"}
                      {checks.length > 1 ? (
                        <span className="font-normal">({checks.length})</span>
                      ) : null}
                    </Link>
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </Panel>
  );
}

/** A perturbation's name as the page shows it: "fein_in_email" reads "FEIN in email". */
function perturbationName(name: string): string {
  return name
    .replace("synthetic: ", "Synthetic: ")
    .replaceAll("_", " ")
    .replace(/\b(fein|ifta|mvr|vin|usdot|dot|pdf)\b/gi, (w) => w.toUpperCase());
}
