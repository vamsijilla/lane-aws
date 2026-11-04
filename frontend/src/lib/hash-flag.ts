"use client";

import { useCallback, useSyncExternalStore } from "react";

function subscribe(notify: () => void) {
  window.addEventListener("hashchange", notify);
  return () => window.removeEventListener("hashchange", notify);
}

/** A boolean kept in the URL hash (#name), so an expanded list survives links and reloads. */
export function useHashFlag(name: string): [boolean, (on: boolean) => void] {
  const on = useSyncExternalStore(
    subscribe,
    () => window.location.hash === `#${name}`,
    () => false,
  );
  const set = useCallback(
    (value: boolean) => {
      const url = value ? `#${name}` : window.location.pathname + window.location.search;
      window.history.replaceState(null, "", url);
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    },
    [name],
  );
  return [on, set];
}
