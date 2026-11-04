"use client";

import clsx from "clsx";
import { useEffect, useState } from "react";

import { getHealth, type Health } from "@/lib/api";

type Status = { kind: "checking" } | { kind: "down" } | { kind: "up"; health: Health };

const POLL_MS = 15_000;

export function ApiStatus() {
  const [status, setStatus] = useState<Status>({ kind: "checking" });

  useEffect(() => {
    const controller = new AbortController();
    const check = () =>
      getHealth(controller.signal)
        .then((health) => setStatus({ kind: "up", health }))
        .catch(() => {
          if (!controller.signal.aborted) setStatus({ kind: "down" });
        });
    void check();
    const timer = setInterval(() => void check(), POLL_MS);
    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, []);

  const { tone, label } = describe(status);
  return (
    <span
      role="status"
      className="inline-flex items-center gap-2 rounded-[var(--radius-cell)] px-2 py-1 text-sm text-graphite"
    >
      <span
        aria-hidden="true"
        className={clsx("size-2 rounded-full", {
          "bg-guide": tone === "ok",
          "bg-warning": tone === "warn",
          "bg-stop": tone === "down",
          "bg-hairline": tone === "idle",
        })}
      />
      {label}
    </span>
  );
}

function describe(status: Status): { tone: "ok" | "warn" | "down" | "idle"; label: string } {
  if (status.kind === "checking") return { tone: "idle", label: "Checking service" };
  if (status.kind === "down") return { tone: "down", label: "Service unreachable" };
  const { health } = status;
  if (health.status !== "ok") return { tone: "down", label: "Database unavailable" };
  if (health.llm === "missing_key") return { tone: "warn", label: "AI not configured" };
  return { tone: "ok", label: "Service ready" };
}
