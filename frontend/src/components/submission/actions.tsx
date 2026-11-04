"use client";

import clsx from "clsx";
import { Check, Copy, Mail, RefreshCw, Send, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { useSubmissionId } from "@/components/submission/shell";
import { Chip } from "@/components/ui/chips";
import { Button, ErrorNote, Panel, Skeleton } from "@/components/ui/primitives";
import type { Schemas } from "@/lib/api";
import { formatDateTime, modelName, personName } from "@/lib/format";
import { useActions, useMarkSent, useRedraft, useSetItemIncluded } from "@/lib/queries";
import { useSession } from "@/lib/session";

const SOURCE: Record<string, string> = {
  rule: "Guideline",
  missing: "Missing",
  conflict: "Conflict",
  finding: "Accepted finding",
};

export function NextActions() {
  const id = useSubmissionId();
  const actions = useActions(id);
  const session = useSession();
  if (!session.can("view_content")) {
    return (
      <ErrorNote title="The agent request is for underwriters">
        It contains personal data from the submission.
      </ErrorNote>
    );
  }
  if (actions.isPending) return <Skeleton lines={14} />;
  if (actions.error)
    return <ErrorNote title="Next actions could not be loaded">{actions.error.message}</ErrorNote>;
  const groups = new Map<string, Schemas["ItemOut"][]>();
  for (const it of actions.data.items)
    groups.set(it.group_label, [...(groups.get(it.group_label) ?? []), it]);

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 gap-6 2xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <Panel
          title="Agent request"
          description="Built from the rules, missing items, open conflicts and accepted findings. Take out anything you do not want to ask."
          flush
        >
          {[...groups.entries()].map(([label, items]) => (
            <section key={label} className="border-b border-hairline last:border-0">
              <h3 className="px-4 pt-3 text-sm font-semibold text-graphite">{label}</h3>
              <ul className="px-2 pt-1 pb-2">
                {items.map((it) => (
                  <RequestItem key={it.id} it={it} />
                ))}
              </ul>
            </section>
          ))}
        </Panel>
        <Draft draft={actions.data.draft} lastSent={actions.data.last_sent ?? null} />
      </div>
    </div>
  );
}

function RequestItem({ it }: { it: Schemas["ItemOut"] }) {
  const id = useSubmissionId();
  const toggle = useSetItemIncluded(id);
  const session = useSession();
  return (
    <li
      className={clsx(
        "flex gap-3 rounded-[var(--radius-cell)] px-2 py-2",
        !it.included && "text-graphite",
      )}
    >
      <input
        type="checkbox"
        checked={it.included}
        disabled={!session.can("review") || toggle.isPending}
        onChange={(e) => toggle.mutate({ itemId: it.id, included: e.target.checked })}
        aria-label={`Include: ${it.text}`}
        className="mt-1 size-4 shrink-0 accent-[var(--interstate)]"
      />
      <div className="min-w-0 flex-1">
        <p className="font-semibold">{it.text}</p>
        {it.details.length ? (
          <ul className="mt-0.5 space-y-0.5 text-sm text-graphite">
            {it.details.map((d) => (
              <li key={d} className="pl-3 -indent-3">
                <span aria-hidden="true">– </span>
                {d}
              </li>
            ))}
          </ul>
        ) : null}
      </div>
      <span className="flex shrink-0 flex-col items-end gap-1">
        {it.stale ? (
          <Chip
            tone="warning"
            title="From an expert review written before the agent's latest reply"
          >
            Stale review
          </Chip>
        ) : null}
        <Chip tone="neutral" title={it.source_refs.join(", ")}>
          {SOURCE[it.source_kind] ?? it.source_kind}
          {it.source_kind === "rule"
            ? ` ${it.source_refs.map((r) => r.replace("R:", "")).join(", ")}`
            : ""}
        </Chip>
      </span>
    </li>
  );
}

function Draft({
  draft,
  lastSent,
}: {
  draft: Schemas["DraftOut"] | null;
  lastSent: Schemas["SentOut"] | null;
}) {
  const id = useSubmissionId();
  const redraft = useRedraft(id);
  const markSent = useMarkSent(id);
  const session = useSession();
  const [copied, setCopied] = useState(false);
  if (!draft) {
    return (
      <Panel title="Email to the agent">
        <Button
          variant="primary"
          icon={RefreshCw}
          busy={redraft.isPending}
          onClick={() => redraft.mutate()}
        >
          Draft the email
        </Button>
      </Panel>
    );
  }
  const mailto = `mailto:${encodeURIComponent(draft.to ?? "")}?subject=${encodeURIComponent(draft.subject)}&body=${encodeURIComponent(draft.body)}`;
  return (
    <Panel
      title="Email to the agent"
      description={
        <span className="inline-flex items-center gap-1.5">
          <ShieldCheck size={13} aria-hidden="true" />A draft. Lane never sends email; copy it or
          open it in your mail app.
        </span>
      }
      actions={
        <>
          <Button
            size="sm"
            icon={copied ? Check : Copy}
            onClick={async () => {
              await navigator.clipboard.writeText(`Subject: ${draft.subject}\n\n${draft.body}`);
              setCopied(true);
              setTimeout(() => setCopied(false), 1800);
            }}
          >
            {copied ? "Copied" : "Copy email"}
          </Button>
          <a
            href={mailto}
            className="inline-flex h-7 items-center gap-1.5 rounded-[var(--radius-cell)] bg-interstate px-2 text-sm font-semibold text-on-interstate hover:bg-interstate/90"
          >
            <Mail size={14} aria-hidden="true" /> Open in mail app
          </a>
          {session.can("review") ? (
            <Button
              size="sm"
              icon={Send}
              busy={markSent.isPending}
              title="Record that you sent this request from your own mail; replies are measured against it"
              onClick={() =>
                markSent.mutate(undefined, {
                  onSuccess: () => toast("Marked as sent. Lane did not send anything."),
                  onError: (e) => toast.error(e.message),
                })
              }
            >
              Mark as sent
            </Button>
          ) : null}
        </>
      }
      flush
    >
      {!draft.current ? (
        <div className="flex items-center justify-between gap-3 border-b border-warning/30 bg-warning-soft px-4 py-2 text-sm">
          <span>The request changed since this draft.</span>
          {session.can("review") ? (
            <Button
              size="sm"
              icon={RefreshCw}
              busy={redraft.isPending}
              onClick={() => redraft.mutate()}
            >
              Redraft
            </Button>
          ) : null}
        </div>
      ) : null}
      <p className="border-b border-hairline px-4 py-2 text-sm text-graphite">
        {lastSent
          ? `Marked sent by ${personName(lastSent.sent_by)}, ${formatDateTime(lastSent.sent_at)}, with ${lastSent.items} items${lastSent.current ? "" : "; the request has changed since"}. Replies are measured against it.`
          : "Not marked sent. Replies are measured against this draft until you mark it sent."}
      </p>
      <dl className="space-y-1 border-b border-hairline px-4 py-3 text-sm">
        <div className="flex gap-2">
          <dt className="w-16 text-graphite">To</dt>
          <dd>{draft.to}</dd>
        </div>
        <div className="flex gap-2">
          <dt className="w-16 text-graphite">Subject</dt>
          <dd className="font-semibold">{draft.subject}</dd>
        </div>
      </dl>
      <pre
        tabIndex={0}
        className="max-h-[70vh] overflow-auto px-4 py-3 font-sans text-[13.5px] leading-relaxed whitespace-pre-wrap"
      >
        {draft.body}
      </pre>
      <p className="border-t border-hairline px-4 py-2 text-xs text-graphite">
        Version {draft.version},{" "}
        {draft.method === "model"
          ? `worded by ${modelName(draft.model ?? "")}; every detail inserted by code`
          : "plain template"}
        {draft.problems.length ? `. ${draft.problems.join(". ")}` : ""}.
      </p>
    </Panel>
  );
}
