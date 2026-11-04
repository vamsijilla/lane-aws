"use client";

/**
 * Data hooks over the typed client. Every shape comes from the generated schema; query keys
 * start with the submission id so one invalidation refreshes a whole submission.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import { api, errorMessage, hasToken, type Schemas } from "@/lib/api";

type Data<T> = { data?: T; error?: unknown; response: Response };

async function unwrap<T>(call: Promise<Data<T>>, what: string): Promise<T> {
  const { data, error, response } = await call;
  if (data === undefined)
    throw new Error(errorMessage(error, `Could not load ${what} (${response.status})`));
  return data;
}

const p = (id: string) => ({ params: { path: { submission_id: id } } });

export const keys = {
  submission: (id: string) => ["submission", id] as const,
  part: (id: string, part: string) => ["submission", id, part] as const,
};

/**
 * How each part of a submission is fetched. The hooks below use these, and so does the
 * background prefetch, so every tab of an open submission is already loaded when it is clicked.
 */
export const fetchPart = {
  overview: (id: string) =>
    unwrap(api.GET("/api/v1/submissions/{submission_id}", p(id)), "the submission"),
  documents: (id: string) =>
    unwrap(api.GET("/api/v1/submissions/{submission_id}/documents", p(id)), "documents"),
  pages: (id: string) =>
    unwrap(api.GET("/api/v1/submissions/{submission_id}/pages", p(id)), "pages"),
  extractions: (id: string) =>
    unwrap(api.GET("/api/v1/submissions/{submission_id}/extractions", p(id)), "extractions"),
  entities: (id: string) =>
    unwrap(api.GET("/api/v1/submissions/{submission_id}/entities", p(id)), "facts"),
  conflicts: (id: string) =>
    unwrap(api.GET("/api/v1/submissions/{submission_id}/conflicts", p(id)), "conflicts"),
  missing: (id: string) =>
    unwrap(api.GET("/api/v1/submissions/{submission_id}/missing", p(id)), "missing items"),
  rules: (id: string) =>
    unwrap(api.GET("/api/v1/submissions/{submission_id}/rules", p(id)), "rules"),
  analysis: (id: string) =>
    unwrap(api.GET("/api/v1/submissions/{submission_id}/analysis", p(id)), "the analysis"),
  actions: (id: string) =>
    unwrap(api.GET("/api/v1/submissions/{submission_id}/actions", p(id)), "next actions"),
  runs: (id: string) => unwrap(api.GET("/api/v1/submissions/{submission_id}/runs", p(id)), "runs"),
  audit: (id: string) =>
    unwrap(
      api.GET("/api/v1/submissions/{submission_id}/audit", {
        params: { path: { submission_id: id }, query: { limit: 300 } },
      }),
      "the audit trail",
    ),
};

type Part = keyof typeof fetchPart;

// Prefetches in flight at once, across the whole app: hovering down the inbox or opening a
// submission never floods the API (docs/DECISIONS.md D115). What a person opens is not limited.
const PREFETCH_MAX = 4;
let prefetching = 0;
const prefetchQueue: (() => void)[] = [];

async function limited<T>(fetch: () => Promise<T>): Promise<T> {
  if (prefetching >= PREFETCH_MAX) await new Promise<void>((go) => prefetchQueue.push(go));
  prefetching++;
  try {
    return await fetch();
  } finally {
    prefetching--;
    prefetchQueue.shift()?.();
  }
}

/**
 * Load every other part of a submission in the background, once the first screen has its data
 * (`ready`), one request at a time, so prefetching never slows what the person is looking at.
 */
export function usePrefetchSubmission(id: string, ready: boolean) {
  const client = useQueryClient();
  useEffect(() => {
    if (!ready) return;
    let stopped = false;
    const order: Part[] = [
      "rules",
      "actions",
      "entities",
      "documents",
      "pages",
      "extractions",
      "runs",
      "audit",
    ];
    void (async () => {
      for (const part of order) {
        if (stopped) return;
        await client.prefetchQuery({
          queryKey: keys.part(id, part),
          queryFn: (): Promise<unknown> => limited<unknown>(() => fetchPart[part](id)),
        });
      }
    })();
    return () => {
      stopped = true;
    };
  }, [client, id, ready]);
}

/** Warm what a submission's first screen needs, for a hover on its row in the inbox. */
export function usePrefetchFirstScreen() {
  const client = useQueryClient();
  return (id: string) => {
    for (const part of ["overview", "conflicts", "missing", "analysis"] as const) {
      void client.prefetchQuery({
        queryKey: keys.part(id, part),
        queryFn: (): Promise<unknown> => limited<unknown>(() => fetchPart[part](id)),
      });
    }
  };
}

export function useOverview(id: string) {
  return useQuery({
    queryKey: keys.part(id, "overview"),
    queryFn: () => fetchPart.overview(id),
    // While the agent's reply is being processed, check back until its run finishes
    refetchInterval: (q) => (q.state.data?.amendment?.in_progress ? 2000 : false),
  });
}

export function useDocuments(id: string) {
  return useQuery({
    queryKey: keys.part(id, "documents"),
    queryFn: () => fetchPart.documents(id),
  });
}

export function usePages(id: string) {
  return useQuery({
    queryKey: keys.part(id, "pages"),
    queryFn: () => fetchPart.pages(id),
    staleTime: 60_000,
  });
}

export function useExtractions(id: string) {
  return useQuery({
    queryKey: keys.part(id, "extractions"),
    queryFn: () => fetchPart.extractions(id),
  });
}

export function useEntities(id: string) {
  return useQuery({
    queryKey: keys.part(id, "entities"),
    queryFn: () => fetchPart.entities(id),
  });
}

export function useConflicts(id: string) {
  return useQuery({
    queryKey: keys.part(id, "conflicts"),
    queryFn: () => fetchPart.conflicts(id),
  });
}

export function useMissing(id: string) {
  return useQuery({
    queryKey: keys.part(id, "missing"),
    queryFn: () => fetchPart.missing(id),
  });
}

export function useRules(id: string) {
  return useQuery({
    queryKey: keys.part(id, "rules"),
    queryFn: () => fetchPart.rules(id),
  });
}

export function useAnalysis(id: string) {
  return useQuery({
    queryKey: keys.part(id, "analysis"),
    queryFn: () => fetchPart.analysis(id),
    // While an approved run is in progress, check back until it lands.
    refetchInterval: (q) => (q.state.data?.status === "running" ? 3000 : false),
  });
}

export function useActions(id: string) {
  return useQuery({
    queryKey: keys.part(id, "actions"),
    queryFn: () => fetchPart.actions(id),
  });
}

export function useAudit(id: string) {
  return useQuery({
    queryKey: keys.part(id, "audit"),
    queryFn: () => fetchPart.audit(id),
  });
}

export function useRuns(id: string) {
  return useQuery({
    queryKey: keys.part(id, "runs"),
    queryFn: () => fetchPart.runs(id),
    // Follow a run (a replay, say) until it finishes
    refetchInterval: (q) => (q.state.data?.some((r) => r.status === "running") ? 2000 : false),
  });
}

export function useField(fieldId: string | null) {
  return useQuery({
    queryKey: ["field", fieldId],
    enabled: hasToken() && Boolean(fieldId),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/fields/{field_id}", { params: { path: { field_id: fieldId ?? "" } } }),
        "the evidence",
      ),
  });
}

export function useFacts(ids: string[]) {
  return useQuery({
    queryKey: ["facts", ids.join(",")],
    enabled: hasToken() && ids.length > 0,
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/facts", { params: { query: { ids: [ids.join(",")] } } }),
        "the evidence",
      ),
  });
}

export function useDocumentFacts(documentId: string | null) {
  return useQuery({
    queryKey: ["document-facts", documentId],
    enabled: hasToken() && Boolean(documentId),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/documents/{document_id}/facts", {
          params: { path: { document_id: documentId ?? "" } },
        }),
        "facts",
      ),
  });
}

export function useRuleset() {
  return useQuery({
    queryKey: ["ruleset"],
    queryFn: () => unwrap(api.GET("/api/v1/ruleset"), "the ruleset"),
    staleTime: Infinity,
  });
}

export function useEvals() {
  return useQuery({
    queryKey: ["evals"],
    queryFn: () => unwrap(api.GET("/api/v1/evals"), "eval reports"),
  });
}

/** How Lane works, with one submission's own numbers. */
export function useHow(id: string | null) {
  return useQuery({
    queryKey: ["how", id],
    enabled: hasToken() && Boolean(id),
    queryFn: () =>
      unwrap(api.GET("/api/v1/submissions/{submission_id}/how", p(id ?? "")), "how Lane works"),
  });
}

export function usePerturbations() {
  return useQuery({
    queryKey: ["perturbations"],
    queryFn: () => unwrap(api.GET("/api/v1/evals/perturbations"), "the perturbation report"),
    retry: false,
  });
}

export function useOpsQueue(enabled: boolean) {
  return useQuery({
    queryKey: ["ops-queue"],
    queryFn: () => unwrap(api.GET("/api/v1/ops/queue"), "the work queue"),
    refetchInterval: 2000,
    enabled: hasToken() && enabled,
  });
}

export function useOps() {
  return useQuery({
    queryKey: ["ops"],
    queryFn: () => unwrap(api.GET("/api/v1/ops/health"), "operations health"),
    refetchInterval: 10_000,
  });
}

// --- mutations ---------------------------------------------------------------------------

export function useReviewFinding(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { findingId: string } & Schemas["ReviewIn"]) =>
      unwrap(
        api.POST("/api/v1/findings/{finding_id}/review", {
          params: { path: { finding_id: v.findingId } },
          body: { state: v.state, reason: v.reason },
        }),
        "the review",
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.submission(id) }),
  });
}

export function useSetItemIncluded(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { itemId: string; included: boolean }) =>
      unwrap(
        api.PATCH("/api/v1/request-items/{item_id}", {
          params: { path: { item_id: v.itemId } },
          body: { included: v.included },
        }),
        "the request item",
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.submission(id) }),
  });
}

export function useRedraft(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/v1/submissions/{submission_id}/actions/redraft", p(id)), "the draft"),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.submission(id) }),
  });
}

/** A person sent the request from their own mail: snapshot it. Lane sends nothing. */
export function useMarkSent(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/submissions/{submission_id}/actions/mark-sent", p(id)),
        "the sent mark",
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.submission(id) }),
  });
}

/** An underwriter confirms a rule outcome the agent's documents moved away from decline. */
export function useConfirmRuleChange(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (confirmationId: string) =>
      unwrap(
        api.POST("/api/v1/rule-confirmations/{confirmation_id}/confirm", {
          params: { path: { confirmation_id: confirmationId } },
        }),
        "the confirmation",
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.submission(id) });
      void qc.invalidateQueries({ queryKey: ["submissions"] });
    },
  });
}

/** The underwriter's decision; Lane's recommendation stays beside it. */
export function useRecordDecision(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { decision: string; reason: string; conditions: string[] }) =>
      unwrap(
        api.POST("/api/v1/submissions/{submission_id}/decision", { ...p(id), body }),
        "the decision",
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.submission(id) });
      void qc.invalidateQueries({ queryKey: ["submissions"] });
    },
  });
}

export function useRequestSeniorReview(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (action: "quote" | "confirm") =>
      unwrap(
        api.POST("/api/v1/submissions/{submission_id}/senior-review", {
          ...p(id),
          body: { action },
        }),
        "the review request",
      ),
    onSuccess: async (submission) => {
      // The request shows at once on the Brief. A fetch already in flight (from sign-in, say)
      // would land later with the old submission, so it is cancelled first; then the Brief and
      // the Inbox reload from the server
      const overview = keys.part(id, "overview");
      await qc.cancelQueries({ queryKey: overview });
      qc.setQueryData(overview, (old: Schemas["OverviewOut"] | undefined) =>
        old ? { ...old, submission } : old,
      );
      void qc.invalidateQueries({ queryKey: overview });
      void qc.invalidateQueries({ queryKey: ["submissions"] });
    },
  });
}

export function useEmail(id: string, emailId: string) {
  return useQuery({
    queryKey: keys.part(id, `email:${emailId}`),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/submissions/{submission_id}/emails/{email_id}", {
          params: { path: { submission_id: id, email_id: emailId } },
        }),
        "the email",
      ),
  });
}

export function useReveal() {
  return useMutation({
    mutationFn: (fieldId: string) =>
      unwrap(
        api.POST("/api/v1/fields/{field_id}/reveal", { params: { path: { field_id: fieldId } } }),
        "the value",
      ),
  });
}

export type { Schemas };

export function useResolveConflict(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { conflictId: string; candidate: number; reason: string }) =>
      unwrap(
        api.POST("/api/v1/conflicts/{conflict_id}/resolve", {
          params: { path: { conflict_id: v.conflictId } },
          body: { candidate: v.candidate, reason: v.reason },
        }),
        "the resolution",
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.submission(id) }),
  });
}

export function useReopenConflict(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (conflictId: string) =>
      unwrap(
        api.DELETE("/api/v1/conflicts/{conflict_id}/resolution", {
          params: { path: { conflict_id: conflictId } },
        }),
        "the conflict",
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.submission(id) }),
  });
}

export function useApproveAnalysis(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      unwrap(
        api.POST("/api/v1/submissions/{submission_id}/analysis/approve", p(id)),
        "the approval",
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.submission(id) }),
  });
}

/** Correct a value with a reason; resolution and the rules run again. */
export function useOverrideField(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { fieldId: string; value: string; reason: string }) =>
      unwrap(
        api.POST("/api/v1/fields/{field_id}/override", {
          params: { path: { field_id: v.fieldId } },
          body: { value: v.value, reason: v.reason },
        }),
        "the correction",
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.submission(id) });
      void qc.invalidateQueries({ queryKey: ["field"] });
    },
  });
}

export function useClearOverride(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (fieldId: string) =>
      unwrap(
        api.DELETE("/api/v1/fields/{field_id}/override", {
          params: { path: { field_id: fieldId } },
        }),
        "the correction",
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.submission(id) });
      void qc.invalidateQueries({ queryKey: ["field"] });
    },
  });
}

/** Replay every step from the stored files, from the cache only, and compare the output. */
export function useReplay(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      unwrap(api.POST("/api/v1/submissions/{submission_id}/replay", p(id)), "the replay"),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.part(id, "runs") }),
  });
}

export function useDocumentTypes() {
  return useQuery({
    queryKey: ["document-types"],
    queryFn: () => unwrap(api.GET("/api/v1/document-types"), "document types"),
    staleTime: Infinity,
  });
}

/** Say what a document is; the submission is processed again so it is read as that type. */
/** A person confirms what a document is; Lane learns its layout from it (tier 2). */
/** A person attaches a suggested follow-up to the earlier submission, or keeps it apart. */
export function useFollowUpDecision(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (attach: boolean) =>
      attach
        ? unwrap(api.POST("/api/v1/submissions/{submission_id}/attach", p(id)), "the attachment")
        : unwrap(
            api.POST("/api/v1/submissions/{submission_id}/keep-separate", p(id)),
            "the decision",
          ),
    onSuccess: (out) => {
      void qc.invalidateQueries({ queryKey: keys.submission(id) });
      void qc.invalidateQueries({ queryKey: keys.submission(out.submission_id) });
      void qc.invalidateQueries({ queryKey: ["submissions"] });
    },
  });
}

export function useConfirmDocument(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (documentId: string) =>
      unwrap(
        api.POST("/api/v1/documents/{document_id}/confirm", {
          params: { path: { document_id: documentId } },
        }),
        "the confirmation",
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: keys.submission(id) });
      void qc.invalidateQueries({ queryKey: ["known-layouts"] });
    },
  });
}

export function useLabelDocument(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { documentId: string; docType: string; reason: string }) =>
      unwrap(
        api.POST("/api/v1/documents/{document_id}/label", {
          params: { path: { document_id: v.documentId } },
          body: { doc_type: v.docType, reason: v.reason },
        }),
        "the label",
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.submission(id) }),
  });
}

export function useClearLabel(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (documentId: string) =>
      unwrap(
        api.DELETE("/api/v1/documents/{document_id}/label", {
          params: { path: { document_id: documentId } },
        }),
        "the label",
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.submission(id) }),
  });
}

export function useKnownLayouts() {
  return useQuery({
    queryKey: ["known-layouts"],
    queryFn: () => unwrap(api.GET("/api/v1/known-layouts"), "known layouts"),
  });
}
