"use client";

import * as Dialog from "@radix-ui/react-dialog";
import { useQuery } from "@tanstack/react-query";
import { Command } from "cmdk";
import {
  BookOpen,
  Car,
  FileText,
  Gauge,
  Inbox,
  Keyboard,
  Link2,
  Rows3,
  ListChecks,
  type LucideIcon,
  Scale,
  SunMoon,
  TriangleAlert,
  User,
} from "lucide-react";
import { usePathname, useRouter } from "next/navigation";

import { VIEWS } from "@/components/submission/shell";
import { OUTCOMES } from "@/components/ui/chips";
import { hasToken, listSubmissions } from "@/lib/api";
import { applyDensity, readDensity } from "@/lib/density";
import { fetchPart, keys } from "@/lib/queries";
import { revealHash } from "@/lib/reveal";
import { applyThemeChoice, nextThemeChoice, readThemeChoice } from "@/lib/theme";
import { toast } from "sonner";

/**
 * ⌘K: go anywhere in two keystrokes. Lists the open submission's tabs, rules (by id), conflicts,
 * drivers and vehicles, every submission in the inbox, the app pages, and a few actions. Reads
 * the same cached queries the pages use, so it opens instantly and never fetches twice.
 */
export function CommandPalette({
  open,
  onOpenChange,
  onShowShortcuts,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onShowShortcuts: () => void;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const sid = /^\/submissions\/([^/]+)/.exec(pathname)?.[1] ?? null;
  const base = sid ? `/submissions/${sid}` : "";
  const on = open && Boolean(sid);

  const submissions = useQuery({
    queryKey: ["submissions"],
    queryFn: ({ signal }) => listSubmissions(signal),
    enabled: hasToken() && open,
  });
  const rules = useQuery({
    queryKey: keys.part(sid ?? "", "rules"),
    queryFn: () => fetchPart.rules(sid ?? ""),
    enabled: hasToken() && on,
  });
  const conflicts = useQuery({
    queryKey: keys.part(sid ?? "", "conflicts"),
    queryFn: () => fetchPart.conflicts(sid ?? ""),
    enabled: hasToken() && on,
  });
  const entities = useQuery({
    queryKey: keys.part(sid ?? "", "entities"),
    queryFn: () => fetchPart.entities(sid ?? ""),
    enabled: hasToken() && on,
  });

  const go = (href: string) => {
    onOpenChange(false);
    router.push(href);
    const hash = href.split("#")[1];
    if (hash) revealHash(hash); // the item, once its tab has rendered it
  };
  const drivers = (entities.data ?? []).filter((e) => e.kind === "driver");
  const vehicles = (entities.data ?? []).filter((e) => e.kind === "vehicle");
  const openConflicts = (conflicts.data ?? []).filter((c) => !c.resolution);

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/40 backdrop-blur-[2px] data-[state=open]:animate-[fade-in_120ms_ease-out]" />
        <Dialog.Content
          aria-describedby={undefined}
          className="fixed top-[14vh] left-1/2 z-50 w-[min(40rem,calc(100vw-2rem))] -translate-x-1/2 overflow-hidden rounded-[var(--radius-panel)] border border-hairline bg-surface shadow-[0_24px_64px_-16px_rgb(0_0_0/0.5)] data-[state=open]:animate-[palette-in_140ms_ease-out]"
        >
          <Dialog.Title className="sr-only">Go to</Dialog.Title>
          <Command label="Go to" loop className="flex max-h-[min(32rem,70vh)] flex-col">
            <div className="flex items-center gap-2 border-b border-hairline px-4">
              <Command.Input
                autoFocus
                placeholder={
                  sid
                    ? "Go to a tab, rule (DRV-01), conflict, driver, vehicle or submission"
                    : "Go to a submission or page"
                }
                className="h-12 flex-1 bg-transparent text-base outline-none placeholder:text-graphite focus-visible:outline-none"
              />
              <kbd className="rounded-[4px] border border-hairline px-1.5 text-xs text-graphite">
                Esc
              </kbd>
            </div>
            <Command.List className="overflow-y-auto overscroll-contain p-2 [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:pt-2 [&_[cmdk-group-heading]]:pb-1 [&_[cmdk-group-heading]]:text-xs [&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:text-graphite">
              <Command.Empty className="px-3 py-6 text-center text-graphite">
                Nothing matches.
              </Command.Empty>
              {sid ? (
                <Command.Group heading="This submission">
                  {VIEWS.flatMap((v) =>
                    v.tabs.map((t) => (
                      <Item
                        key={`tab-${t.href}`}
                        icon={FileText}
                        value={`tab ${v.label} ${t.label}`}
                        onSelect={() => go(`${base}${t.href}`)}
                        hint={v.tabs.length > 1 ? v.label : undefined}
                      >
                        {t.label}
                      </Item>
                    )),
                  )}
                </Command.Group>
              ) : null}
              {rules.data?.rules.length ? (
                <Command.Group heading="Rules">
                  {rules.data.rules.map((r) => (
                    <Item
                      key={r.rule_id}
                      icon={Scale}
                      value={`rule ${r.rule_id} ${r.title}`}
                      onSelect={() => go(`${base}/rules#${r.rule_id}`)}
                      hint={OUTCOMES[r.outcome]?.label ?? r.outcome}
                    >
                      <span className="font-mono text-sm">{r.rule_id}</span> {r.title}
                    </Item>
                  ))}
                </Command.Group>
              ) : null}
              {openConflicts.length ? (
                <Command.Group heading="Conflicts">
                  {openConflicts.map((c) => (
                    <Item
                      key={c.id}
                      icon={TriangleAlert}
                      value={`conflict ${c.title}`}
                      onSelect={() => go(`${base}/conflicts#conflict-${c.id}`)}
                      hint={`${c.severity} severity`}
                    >
                      {c.title}
                    </Item>
                  ))}
                </Command.Group>
              ) : null}
              {drivers.length ? (
                <Command.Group heading="Drivers">
                  {drivers.map((d) => (
                    <Item
                      key={d.id}
                      icon={User}
                      value={`driver ${d.label}`}
                      onSelect={() => go(`${base}/drivers#driver-${d.id}`)}
                    >
                      {d.label}
                    </Item>
                  ))}
                </Command.Group>
              ) : null}
              {vehicles.length ? (
                <Command.Group heading="Vehicles">
                  {vehicles.map((v) => (
                    <Item
                      key={v.id}
                      icon={Car}
                      value={`vehicle ${v.label}`}
                      onSelect={() => go(`${base}/vehicles#vehicle-${v.id}`)}
                    >
                      {v.label}
                    </Item>
                  ))}
                </Command.Group>
              ) : null}
              {submissions.data?.length ? (
                <Command.Group heading="Submissions">
                  {submissions.data.map((s) => (
                    <Item
                      key={s.id}
                      icon={Inbox}
                      value={`submission ${s.subject} ${s.sender_email} ${s.id}`}
                      onSelect={() => go(`/submissions/${s.id}`)}
                      hint={s.recommendation_headline ?? undefined}
                    >
                      {s.subject || "(no subject)"}
                    </Item>
                  ))}
                </Command.Group>
              ) : null}
              <Command.Group heading="Pages">
                <Item icon={Inbox} value="page inbox" onSelect={() => go("/")}>
                  Inbox
                </Item>
                <Item icon={Gauge} value="page evals" onSelect={() => go("/evals")}>
                  Evals
                </Item>
                <Item
                  icon={BookOpen}
                  value="page rules library guideline"
                  onSelect={() => go("/rules-library")}
                >
                  Rules library
                </Item>
                <Item icon={ListChecks} value="page operations ops" onSelect={() => go("/ops")}>
                  Operations
                </Item>
              </Command.Group>
              <Command.Group heading="Actions">
                <Item
                  icon={Link2}
                  value="action copy link to this page"
                  onSelect={() => {
                    onOpenChange(false);
                    void navigator.clipboard
                      .writeText(window.location.href)
                      .then(() => toast.success("Link copied"));
                  }}
                >
                  Copy link to this page
                </Item>
                <Item
                  icon={SunMoon}
                  value="action theme light dark"
                  onSelect={() => {
                    const next = nextThemeChoice(readThemeChoice());
                    applyThemeChoice(next);
                    toast(`Theme: ${next === "system" ? "as the system" : next}`);
                  }}
                >
                  Switch theme
                </Item>
                <Item
                  icon={Rows3}
                  value="action density compact comfortable"
                  onSelect={() => {
                    const next = readDensity() === "compact" ? "comfortable" : "compact";
                    applyDensity(next);
                    toast(`Density: ${next}`);
                  }}
                >
                  Switch density
                </Item>
                <Item
                  icon={Keyboard}
                  value="action keyboard shortcuts help"
                  onSelect={() => {
                    onOpenChange(false);
                    onShowShortcuts();
                  }}
                  hint="?"
                >
                  Keyboard shortcuts
                </Item>
              </Command.Group>
            </Command.List>
          </Command>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function Item({
  icon: Icon,
  value,
  onSelect,
  hint,
  children,
}: {
  icon: LucideIcon;
  value: string;
  onSelect: () => void;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <Command.Item
      value={value}
      onSelect={onSelect}
      className="flex cursor-pointer items-center gap-3 rounded-[var(--radius-cell)] px-2 py-2 text-base data-[selected=true]:bg-surface-sunk"
    >
      <Icon size={16} className="shrink-0 text-graphite" aria-hidden="true" />
      <span className="min-w-0 flex-1 truncate">{children}</span>
      {hint ? <span className="shrink-0 truncate text-sm text-graphite">{hint}</span> : null}
    </Command.Item>
  );
}
