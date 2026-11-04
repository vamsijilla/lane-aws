"use client";

import { useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { finishSignIn, startSignIn } from "@/lib/cognito";
import { useSession } from "@/lib/session";

/** Cognito sends people back here with a code; this finishes signing in, once. */
export function SignInReturn() {
  const params = useSearchParams();
  const session = useSession();
  const [problem, setProblem] = useState<string | null>(null);
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const code = params.get("code");
    const state = params.get("state");
    const denied = params.get("error_description") ?? params.get("error");
    if (denied || !code || !state) {
      // Set after the first render, not during it, so the effect does not loop
      queueMicrotask(() => setProblem(denied ?? "Sign-in did not finish."));
      return;
    }
    finishSignIn(code, state)
      .then(({ idToken, returnTo }) => session.signInWithCognito(idToken, returnTo))
      .catch((e: unknown) => setProblem(e instanceof Error ? e.message : "Sign-in failed."));
  }, [params, session]);

  if (!problem) return <p className="text-graphite">Signing in</p>;
  return (
    <div className="max-w-md space-y-3">
      <h1 className="font-serif text-2xl font-medium">Could not sign in</h1>
      <p className="text-sm text-graphite">{problem}</p>
      <button
        type="button"
        onClick={() => void startSignIn()}
        className="rounded-[var(--radius-control)] bg-interstate px-3 py-2 text-sm font-semibold text-white hover:brightness-110"
      >
        Sign in again
      </button>
    </div>
  );
}
