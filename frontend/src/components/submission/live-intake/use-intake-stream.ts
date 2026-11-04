"use client";

import { useEffect, useReducer } from "react";

import { EVENTS, INITIAL, reduce, type State } from "@/components/submission/live-intake/reducer";
import { authedUrl } from "@/lib/api";

/** Follows a submission's event stream and reduces it to the live intake state. */
export function useIntakeStream(id: string): State {
  const [s, dispatch] = useReducer(reduce, INITIAL);
  useEffect(() => {
    const source = new EventSource(authedUrl(`/api/v1/submissions/${id}/events`));
    const handlers = EVENTS.map((type) => {
      const h = (m: MessageEvent<string>) =>
        dispatch({ type, data: JSON.parse(m.data) as Record<string, unknown> });
      source.addEventListener(type, h);
      return [type, h] as const;
    });
    source.addEventListener("end", () => source.close());
    return () => {
      for (const [type, h] of handlers) source.removeEventListener(type, h);
      source.close();
    };
  }, [id]);
  return s;
}
