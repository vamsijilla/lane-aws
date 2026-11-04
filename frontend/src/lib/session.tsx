"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useMemo, useState } from "react";

import { api, errorMessage, setToken, type Schemas } from "@/lib/api";
import { cognitoEnabled, signOutUrl } from "@/lib/cognito";

const STORAGE_KEY = "lane.session";

export type Role = Schemas["LoginIn"]["role"];
export type Person = Schemas["PersonOut"];

type Session = {
  role: string;
  roleLabel: string;
  name: string | null;
  signedIn: boolean;
  permissions: string[];
  can: (permission: string) => boolean;
  /** Decision authority by role (settings.decision_authority on the API) */
  mayDecide: (action: string) => boolean;
  signIn: (person: Person) => Promise<void>;
  /** Real sign-in: exchange Cognito's ID token for a Lane session, then go to returnTo */
  signInWithCognito: (idToken: string, returnTo: string) => Promise<void>;
  signOut: () => Promise<void>;
  ready: boolean;
};

const SessionContext = createContext<Session | null>(null);

function remember(token: string | null) {
  try {
    if (token) window.localStorage.setItem(STORAGE_KEY, token);
    else window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Storage unavailable (private mode): this tab is still signed in, or out
  }
}

/** Demo sign-in as one of the demo's people. The token lives in localStorage until Sign out;
 * production signs in with SSO instead (docs/SECURITY.md). */
export function SessionProvider({ children }: { children: React.ReactNode }) {
  const qc = useQueryClient();
  // Restore a saved sign-in before the first request. On the server there is nothing to restore.
  useState(() => {
    if (typeof window === "undefined") return null;
    try {
      const token = window.localStorage.getItem(STORAGE_KEY);
      if (token) setToken(token);
      return token;
    } catch {
      return null;
    }
  });
  const ready = typeof window !== "undefined";

  const me = useQuery({
    queryKey: ["session"],
    enabled: ready,
    queryFn: async () => {
      const { data, error } = await api.GET("/api/v1/auth/me");
      if (!data) {
        // An expired or invalid token: drop it, and the sign-in page shows
        setToken(null);
        remember(null);
        const anon = await api.GET("/api/v1/auth/me");
        if (anon.data) return anon.data;
        throw new Error(errorMessage(error, "Sign-in expired"));
      }
      return data;
    },
    retry: 1,
  });

  const signIn = useCallback(
    async (person: Person) => {
      const { data, error } = await api.POST("/api/v1/auth/demo-login", {
        body: { role: person.role as Role, person: person.id },
      });
      if (!data?.token) throw new Error(errorMessage(error, "Could not sign in"));
      // A clean start as this person, the same as Switch and Sign out: nothing cached is kept
      // and the page loads again, signed in
      remember(data.token);
      qc.clear();
      window.location.assign(window.location.pathname + window.location.search);
    },
    [qc],
  );

  const signInWithCognito = useCallback(
    async (idToken: string, returnTo: string) => {
      const { data, error } = await api.POST("/api/v1/auth/cognito", {
        body: { id_token: idToken },
      });
      if (!data?.token) throw new Error(errorMessage(error, "Could not sign in"));
      remember(data.token);
      qc.clear();
      window.location.assign(returnTo);
    },
    [qc],
  );

  const signOut = useCallback(async () => {
    // Nothing of this person's is kept: the whole cache goes and the page reloads, which shows
    // the sign-in screen (there is no session to restore)
    setToken(null);
    remember(null);
    qc.clear();
    // With real sign-in, Cognito's own session ends too, so the next person signs in afresh
    window.location.assign(cognitoEnabled() ? signOutUrl() : window.location.pathname);
  }, [qc]);

  const value = useMemo<Session>(() => {
    const permissions = me.data?.permissions ?? [];
    const authority = me.data?.authority ?? [];
    return {
      role: me.data?.role ?? "viewer",
      roleLabel: me.data?.role_label ?? "Viewer",
      name: me.data?.name ?? null,
      signedIn: me.data?.signed_in ?? false,
      permissions,
      can: (perm: string) => permissions.includes(perm),
      mayDecide: (action: string) => authority.includes(action),
      signIn,
      signInWithCognito,
      signOut,
      ready: ready && me.isSuccess,
    };
  }, [me.data, me.isSuccess, ready, signIn, signInWithCognito, signOut]);

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): Session {
  const s = useContext(SessionContext);
  if (!s) throw new Error("useSession outside SessionProvider");
  return s;
}
