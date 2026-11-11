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
export function useStartHealthyComparison(runId: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: StartHealthyComparison) => startHealthyComparison(runId, body),
    onSuccess: (found) => client.setQueryData(["healthy-comparison", runId], found),
  });
}
