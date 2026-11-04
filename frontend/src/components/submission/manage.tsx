"use client";

import { useQueryClient } from "@tanstack/react-query";
import { RotateCw, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { api, errorMessage } from "@/lib/api";
import { useSession } from "@/lib/session";

type Action = "delete" | "restart";

/** Delete this submission, or throw it away and ingest the original email again. Both open a
 * dialog that names the submission; Delete also asks for the applicant name to be typed. */
export function ManageSubmission({ id, name }: { id: string; name: string }) {
  const session = useSession();
  const router = useRouter();
  const qc = useQueryClient();
  const dialog = useRef<HTMLDialogElement>(null);
  const [action, setAction] = useState<Action | null>(null);
  const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (action) dialog.current?.showModal();
    else dialog.current?.close();
  }, [action]);

  if (!session.can("delete")) return null;

  const matches = typed.trim().toLowerCase() === name.trim().toLowerCase() && name.trim() !== "";
  const ready = action === "restart" || matches;

  function open(a: Action) {
    setTyped("");
    setError(null);
    setAction(a);
  }

  async function confirm() {
    if (!action || !ready) return;
    setBusy(true);
    setError(null);
    try {
      if (action === "delete") {
        const { error: e, response } = await api.DELETE("/api/v1/submissions/{submission_id}", {
          params: { path: { submission_id: id } },
        });
        if (!response.ok) throw new Error(errorMessage(e, "Could not delete"));
        qc.clear();
        router.push("/");
      } else {
        const { data, error: e } = await api.POST("/api/v1/submissions/{submission_id}/restart", {
          params: { path: { submission_id: id } },
        });
        if (!data) throw new Error(errorMessage(e, "Could not start over"));
        qc.clear();
        router.push(`/submissions/${data.submission_id}`);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong");
      setBusy(false);
    }
  }

  const title = action === "delete" ? `Delete ${name}?` : `Start over with ${name}?`;
  return (
    <>
      <div className="-my-1 flex items-center gap-1">
        <button
          type="button"
          onClick={() => open("restart")}
          className="inline-flex items-center gap-1.5 rounded-[var(--radius-cell)] px-2.5 py-1 text-sm font-semibold text-graphite hover:bg-surface-sunk hover:text-ink"
        >
          <RotateCw size={14} aria-hidden="true" /> Start over
        </button>
        <button
          type="button"
          onClick={() => open("delete")}
          className="inline-flex items-center gap-1.5 rounded-[var(--radius-cell)] px-2.5 py-1 text-sm font-semibold text-graphite hover:bg-surface-sunk hover:text-ink"
        >
          <Trash2 size={14} aria-hidden="true" /> Delete
        </button>
      </div>
      <dialog
        ref={dialog}
        aria-labelledby="manage-title"
        onClose={() => !busy && setAction(null)}
        className="m-auto w-[min(92vw,480px)] rounded-[var(--radius-panel)] border border-hairline bg-surface p-0 text-ink shadow-xl backdrop:bg-ink/40"
      >
        <form
          method="dialog"
          className="space-y-4 p-6"
          onSubmit={(e) => {
            e.preventDefault();
            void confirm();
          }}
        >
          <h2 id="manage-title" className="font-display text-lg font-bold">
            {title}
          </h2>
          <p className="text-sm text-graphite">
            {action === "delete"
              ? "This deletes the submission and everything read from it. It cannot be undone."
              : "This deletes the submission and reads the original email again, with every file. Corrections and decisions made here are lost."}
          </p>
          {action === "delete" ? (
            <label className="block space-y-2 text-sm">
              <span className="block">
                Type <span className="font-semibold">{name}</span> to confirm
              </span>
              <input
                autoFocus
                value={typed}
                onChange={(e) => setTyped(e.target.value)}
                className="w-full rounded-[var(--radius-cell)] border border-hairline bg-paper px-3 py-2 text-ink focus:outline-2 focus:outline-interstate"
                aria-label="Applicant name"
              />
            </label>
          ) : null}
          {error ? (
            <p role="alert" className="text-sm text-stop">
              {error}
            </p>
          ) : null}
          <div className="flex justify-end gap-2">
            <button
              type="button"
              disabled={busy}
              onClick={() => setAction(null)}
              className="rounded-[var(--radius-cell)] px-3 py-1.5 text-sm font-semibold text-graphite hover:bg-surface-sunk"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={busy || !ready}
              className={
                action === "delete"
                  ? "rounded-[var(--radius-control)] bg-stop px-3.5 py-1.5 text-sm font-semibold text-white disabled:opacity-50"
                  : "rounded-[var(--radius-control)] bg-interstate px-3.5 py-1.5 text-sm font-semibold text-on-interstate disabled:opacity-50"
              }
            >
              {busy ? "Working" : action === "delete" ? "Delete" : "Start over"}
            </button>
          </div>
        </form>
      </dialog>
    </>
  );
}
