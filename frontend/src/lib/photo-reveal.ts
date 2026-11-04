"use client";

import { useSyncExternalStore } from "react";

/** License photos an underwriter revealed in this browser session. A reveal lasts until the tab
 * session ends; the API records each one in the audit trail when the photo is fetched. */
const KEY = "lane.revealed-photos";
const listeners = new Set<() => void>();

function read(): string[] {
  try {
    return JSON.parse(sessionStorage.getItem(KEY) ?? "[]") as string[];
  } catch {
    return [];
  }
}

let snapshot = "";

function current(): string {
  const next = typeof window === "undefined" ? "" : read().join(",");
  if (next !== snapshot) snapshot = next;
  return snapshot;
}

export function revealPhoto(pageId: string): void {
  try {
    const ids = new Set(read());
    ids.add(pageId);
    sessionStorage.setItem(KEY, JSON.stringify([...ids]));
  } catch {
    // storage unavailable: the reveal still holds until the page reloads
    snapshot = `${snapshot},${pageId}`;
  }
  for (const l of listeners) l();
}

export function useRevealedPhoto(pageId: string): boolean {
  const ids = useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    current,
    () => "",
  );
  return ids.split(",").includes(pageId);
}
