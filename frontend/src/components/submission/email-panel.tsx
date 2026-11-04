"use client";

import { X } from "lucide-react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import Link from "next/link";
import { useEffect, useRef, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";

import { ErrorNote, Skeleton } from "@/components/ui/primitives";
import { formatDate, formatTime, plural } from "@/lib/format";
import { useEmail } from "@/lib/queries";

// How a reply was matched to this submission, as a sentence
export const MATCHED: Record<string, string> = {
  reference: "Matched by the reference in the subject",
  thread: "Matched by the email thread",
  identity: "Matched by the DOT, MC or FEIN",
  name: "Matched by the applicant's name only",
  person: "Attached by a person",
};

/** "Nov 2, 2026, 3:08 PM" */
export function emailWhen(value: string): string {
  return `${formatDate(value)}, ${formatTime(value)}`;
}

/** One email as it arrived, beside the page like the evidence lens. Esc closes. */
export function EmailPanel({
  submissionId,
  emailId,
  onClose,
}: {
  submissionId: string;
  emailId: string | null;
  onClose: () => void;
}) {
  const reduce = useReducedMotion();
  // Into the body, so a sticky column it is opened from cannot trap it under the page's tabs
  const mounted = useSyncExternalStore(
    noop,
    () => true,
    () => false,
  );
  if (!mounted) return null;
  return createPortal(
    <AnimatePresence>
      {emailId ? (
        <motion.aside
          key={emailId}
          role="dialog"
          aria-modal="false"
          aria-label="Email"
          initial={reduce ? { opacity: 0 } : { x: 48, opacity: 0 }}
          animate={reduce ? { opacity: 1 } : { x: 0, opacity: 1 }}
          exit={reduce ? { opacity: 0 } : { x: 48, opacity: 0 }}
          transition={{ duration: 0.18, ease: [0.2, 0.8, 0.2, 1] }}
          className="fixed top-14 right-0 bottom-0 z-40 flex w-[min(46vw,760px)] min-w-[min(520px,100vw)] flex-col border-l border-hairline bg-surface shadow-[var(--shadow-overlay)]"
        >
          <EmailBody submissionId={submissionId} emailId={emailId} onClose={onClose} />
        </motion.aside>
      ) : null}
    </AnimatePresence>,
    document.body,
  );
}

const noop = () => () => {};

function EmailBody({
  submissionId,
  emailId,
  onClose,
}: {
  submissionId: string;
  emailId: string;
  onClose: () => void;
}) {
  const email = useEmail(submissionId, emailId);
  const close = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    close.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  const e = email.data;
  const base = `/submissions/${submissionId}`;
  return (
    <>
      <div className="flex items-start justify-between gap-3 border-b border-hairline px-5 py-4">
        <div className="min-w-0">
          <p className="text-sm text-graphite">{e?.role === "reply" ? "Reply" : "Email"}</p>
          <h2 className="font-serif text-xl leading-snug font-medium">
            {e ? e.subject || "(no subject)" : " "}
          </h2>
        </div>
        <button
          ref={close}
          type="button"
          onClick={onClose}
          aria-label="Close email (Esc)"
          className="rounded-[var(--radius-cell)] p-1 text-graphite hover:text-ink"
        >
          <X size={18} />
        </button>
      </div>
      <div className="flex-1 space-y-5 overflow-y-auto px-5 py-4 text-sm">
        {email.isPending ? <Skeleton lines={8} /> : null}
        {email.error ? (
          <ErrorNote title="The email could not be loaded">{email.error.message}</ErrorNote>
        ) : null}
        {e ? (
          <>
            <dl className="grid grid-cols-[4.5rem_minmax(0,1fr)] gap-x-3 gap-y-1">
              <dt className="text-graphite">From</dt>
              <dd className="break-words">{e.sender}</dd>
              <dt className="text-graphite">To</dt>
              <dd className="break-words">{e.to.join(", ") || "Not shown"}</dd>
              <dt className="text-graphite">Sent</dt>
              <dd>{emailWhen(e.sent_at)}</dd>
            </dl>

            {e.role === "reply" ? (
              <div className="space-y-1 border-l-2 border-interstate pl-3">
                {e.how ? <p>{MATCHED[e.how] ?? `Matched by ${e.how}`}</p> : null}
                {e.requested ? (
                  <a
                    href="#agent-reply"
                    onClick={onClose}
                    className="text-interstate hover:underline"
                  >
                    Answered {e.answered} of {plural(e.requested, "point")}
                  </a>
                ) : null}
              </div>
            ) : null}

            <section>
              <h3 className="mb-1 text-graphite">Message</h3>
              <pre className="max-h-[40vh] overflow-auto rounded-[var(--radius-cell)] border border-hairline bg-surface-sunk p-3 font-sans text-sm leading-relaxed whitespace-pre-wrap">
                {e.body.trim() || "The email has no text."}
              </pre>
            </section>

            <section>
              <h3 className="mb-1 text-graphite">
                {e.attachments.length
                  ? plural(e.attachments.length, "attachment")
                  : "No attachments"}
              </h3>
              <ul className="divide-y divide-hairline">
                {e.attachments.map((a, i) => (
                  <li key={`${a.filename}-${i}`} className="py-1.5">
                    {a.attachment_id ? (
                      <Link
                        href={`${base}/documents#file-${a.attachment_id}`}
                        onClick={onClose}
                        className="break-words text-interstate hover:underline"
                      >
                        {a.filename}
                      </Link>
                    ) : (
                      <span className="break-words">{a.filename}</span>
                    )}
                    <span className="block text-xs text-graphite">
                      {a.duplicate_of
                        ? `Duplicate of ${a.duplicate_of}`
                        : a.note
                          ? a.note
                          : a.documents
                            ? `Read as ${plural(a.documents, "document")}`
                            : "Not read"}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          </>
        ) : null}
      </div>
    </>
  );
}
