"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { Search } from "lucide-react";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Toaster, toast } from "sonner";

import { CommandPalette } from "@/components/ui/command-palette";
import { Kbd } from "@/components/ui/primitives";
import { listSubmissions } from "@/lib/api";
import { revealHash } from "@/lib/reveal";

type Shortcut = { keys: string[]; does: string };

/** Every shortcut in the app, in one place: the sheet (?) reads this list. */
export const SHORTCUTS: { where: string; items: Shortcut[] }[] = [
  {
    where: "Anywhere",
    items: [
      { keys: ["⌘", "K"], does: "Go to a tab, rule, conflict, driver, vehicle or submission" },
      { keys: ["?"], does: "Show these shortcuts" },
      { keys: ["G", "I"], does: "Inbox" },
      { keys: ["G", "O"], does: "Operations" },
      { keys: ["G", "E"], does: "Evals" },
    ],
  },
  {
    where: "In a submission",
    items: [
      { keys: ["G", "B"], does: "Brief" },
      { keys: ["G", "D"], does: "Documents" },
      { keys: ["G", "C"], does: "Conflicts" },
      { keys: ["G", "R"], does: "Rules" },
      { keys: ["G", "F"], does: "AI findings" },
      { keys: ["G", "A"], does: "Next actions" },
    ],
  },
  {
    where: "Conflicts",
    items: [
      { keys: ["J"], does: "Next conflict" },
      { keys: ["K"], does: "Previous conflict" },
      { keys: ["E"], does: "Open the evidence" },
      { keys: ["1", "–", "9"], does: "Open that value's source" },
    ],
  },
  {
    where: "Evidence lens",
    items: [
      { keys: ["E"], does: "Open the evidence for the focused value" },
      { keys: ["←", "→"], does: "Previous or next source" },
      { keys: ["Esc"], does: "Close" },
    ],
  },
];

const GO_ANYWHERE: Record<string, string> = { i: "/", o: "/ops", e: "/evals" };
const GO_SUBMISSION: Record<string, string> = {
  b: "",
  d: "/documents",
  c: "/conflicts",
  r: "/rules",
  f: "/findings",
  a: "/actions",
};

function typing(target: EventTarget | null): boolean {
  return (
    target instanceof HTMLElement &&
    (target.isContentEditable || Boolean(target.closest("input, textarea, select")))
  );
}

/**
 * Announce what arrives while the person works anywhere in the app: a new agent email, and a
 * submission whose processing finished (with its route). Follows the inbox list every 5 s.
 */
function useArrivalToasts() {
  const router = useRouter();
  const list = useQuery({
    queryKey: ["submissions"],
    queryFn: ({ signal }) => listSubmissions(signal),
    refetchInterval: 5000,
  });
  const seen = useRef<Map<string, string> | null>(null);
  useEffect(() => {
    if (!list.data) return;
    const now = new Map(list.data.map((s) => [s.id, s.status]));
    const before = seen.current;
    seen.current = now;
    if (before === null) return; // first load: nothing has "arrived"
    for (const s of list.data) {
      const open = { label: "Open", onClick: () => router.push(`/submissions/${s.id}`) };
      const was = before.get(s.id);
      if (was === undefined) {
        toast(`New submission: ${s.subject || "(no subject)"}`, {
          description: `From ${s.sender_name ?? s.sender_email}. Reading ${s.files} files.`,
          action: open,
        });
      } else if (was === "processing" && s.status !== "processing") {
        toast(`Processed: ${s.subject || "(no subject)"}`, {
          description: s.recommendation_headline
            ? `${s.recommendation_headline}${s.potential_decline ? ", potential decline" : ""}.`
            : "Ready to review.",
          action: open,
        });
      }
    }
  }, [list.data, router]);
}

/** The palette, the shortcuts sheet, toasts and the global keys, mounted once in the shell. */
export function Commands() {
  const router = useRouter();
  const pathname = usePathname();
  const [palette, setPalette] = useState(false);
  const [sheet, setSheet] = useState(false);
  const leader = useRef<number | null>(null);
  useArrivalToasts();

  // A deep link with a #hash (from the palette or pasted) lands on its item
  useEffect(() => {
    if (window.location.hash) revealHash(window.location.hash);
  }, [pathname]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPalette((o) => !o);
        return;
      }
      if (e.metaKey || e.ctrlKey || e.altKey || typing(e.target)) return;
      if (e.key === "?") {
        e.preventDefault();
        setSheet(true);
        return;
      }
      const key = e.key.toLowerCase();
      if (leader.current !== null && performance.now() - leader.current < 1200) {
        leader.current = null;
        const sid = /^\/submissions\/([^/]+)/.exec(window.location.pathname)?.[1];
        const href =
          GO_ANYWHERE[key] ??
          (sid && GO_SUBMISSION[key] !== undefined
            ? `/submissions/${sid}${GO_SUBMISSION[key]}`
            : null);
        if (href !== null) {
          e.preventDefault();
          router.push(href);
        }
        return;
      }
      if (key === "g") leader.current = performance.now();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [router]);

  return (
    <>
      <button
        type="button"
        onClick={() => setPalette(true)}
        className="inline-flex h-8 shrink-0 items-center gap-2 rounded-[var(--radius-cell)] border border-hairline px-2.5 text-sm text-graphite hover:border-graphite hover:text-ink"
        aria-keyshortcuts="Meta+K Control+K"
      >
        <Search size={14} aria-hidden="true" />
        <span className="sr-only xl:not-sr-only">Go to</span>
        <span className="inline-flex gap-0.5">
          <Kbd>⌘</Kbd>
          <Kbd>K</Kbd>
        </span>
      </button>
      <CommandPalette
        open={palette}
        onOpenChange={setPalette}
        onShowShortcuts={() => setSheet(true)}
      />
      <Dialog.Root open={sheet} onOpenChange={setSheet}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-50 bg-black/40 backdrop-blur-[2px]" />
          <Dialog.Content
            aria-describedby={undefined}
            className="fixed top-1/2 left-1/2 z-50 max-h-[80vh] w-[min(44rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-[var(--radius-panel)] border border-hairline bg-surface p-6 shadow-[0_24px_64px_-16px_rgb(0_0_0/0.5)]"
          >
            <Dialog.Title className="font-display text-lg font-bold">
              Keyboard shortcuts
            </Dialog.Title>
            <div className="mt-4 grid gap-x-8 gap-y-5 sm:grid-cols-2">
              {SHORTCUTS.map((group) => (
                <section key={group.where}>
                  <h3 className="mb-2 text-sm font-semibold text-graphite">{group.where}</h3>
                  <dl className="space-y-1.5">
                    {group.items.map((s) => (
                      <div key={s.does} className="flex items-center justify-between gap-4">
                        <dt className="text-base">{s.does}</dt>
                        <dd className="flex shrink-0 gap-1">
                          {s.keys.map((k) =>
                            k === "–" ? (
                              <span key={k} className="text-graphite">
                                to
                              </span>
                            ) : (
                              <Kbd key={k}>{k}</Kbd>
                            ),
                          )}
                        </dd>
                      </div>
                    ))}
                  </dl>
                </section>
              ))}
            </div>
            <p className="mt-5 text-sm text-graphite">
              Shortcuts with G: press G, then the letter.
            </p>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
      <Toaster
        position="bottom-right"
        closeButton
        toastOptions={{
          classNames: {
            toast:
              "!rounded-[var(--radius-panel)] !border !border-hairline !bg-surface !text-ink !shadow-[0_16px_40px_-16px_rgb(0_0_0/0.45)]",
            description: "!text-graphite",
            actionButton: "!bg-interstate !text-on-interstate",
          },
        }}
      />
    </>
  );
}
