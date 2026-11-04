"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";

import { SignInGate } from "@/components/shell/sign-in";
import { HintProvider } from "@/components/ui/hint";
import { hasToken } from "@/lib/api";
import { SessionProvider } from "@/lib/session";

const OPEN_QUERIES = new Set(["session", "people"]);

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          // A minute fresh: prefetched tabs open from the cache; writes invalidate what they change.
          // Nothing runs before a sign-in except the session itself and the people to sign in as,
          // so nothing fetched signed out is ever cached (sign-in, Switch and Sign out reload)
          queries: {
            staleTime: 60_000,
            refetchOnWindowFocus: false,
            retry: 1,
            enabled: (q) => hasToken() || OPEN_QUERIES.has(String(q.queryKey[0])),
          },
        },
      }),
  );
  return (
    <QueryClientProvider client={client}>
      <SessionProvider>
        <HintProvider>
          <SignInGate>{children}</SignInGate>
        </HintProvider>
      </SessionProvider>
    </QueryClientProvider>
  );
}
