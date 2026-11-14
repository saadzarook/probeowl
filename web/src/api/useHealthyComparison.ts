// Author: @SaadZarook
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchHealthyComparison, startHealthyComparison, type StartHealthyComparison } from "./client";
import { RUN_POLL_MS } from "./useRun";

/** A run's comparison with a known-good subject, polled while one is looking. */
export function useHealthyComparison(runId: string) {
  return useQuery({
    queryKey: ["healthy-comparison", runId],
    queryFn: () => fetchHealthyComparison(runId),
    retry: false,
    refetchInterval: (query) => (query.state.data?.attempt?.status === "running" ? RUN_POLL_MS : false),
  });
}

/** Start a comparison, which replaces the run's last one; its answer starts the polling. */
export function useStartHealthyComparison(runId: string, failingSubject?: string) {
  const client = useQueryClient();
  return useMutation({
    // failingSubject: this run's ID, pasted when its key is PII and stored masked (#66). PII, so
    // closed over rather than a mutation's variables, and gone with the form.
    gcTime: 0,
    mutationFn: (body: StartHealthyComparison) =>
      startHealthyComparison(runId, failingSubject ? { ...body, failing_subject: failingSubject } : body),
    onSuccess: (found) => client.setQueryData(["healthy-comparison", runId], found),
  });
}
