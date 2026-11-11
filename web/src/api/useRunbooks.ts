// Author: @SaadZarook
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchRunbooks, fetchStepsInRun, runStep, tickStep, type RunbookFilters } from "./client";

/** An application's runbooks matching the filters; changing any refetches. */
export function useRunbooks(application: string | undefined, filters: RunbookFilters = {}) {
  return useQuery({
    queryKey: ["runbooks", application, filters],
    queryFn: () => fetchRunbooks(application ?? "", filters),
    enabled: application !== undefined,
    retry: false,
  });
}

/** A runbook's steps with one run's values, ticks and latest results. */
export function useStepsInRun(run: string | undefined, runbook: string | undefined) {
  return useQuery({
    queryKey: ["runbook-steps", run, runbook],
    queryFn: () => fetchStepsInRun(run ?? "", runbook ?? ""),
    enabled: run !== undefined && runbook !== undefined,
    retry: false,
  });
}

/** Run one step by its ID; the server has its SQL and its values. */
export function useRunStep(run: string, runbook: string, step: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (acknowledged: boolean) => runStep(run, runbook, step, acknowledged),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["runbook-steps", run, runbook] }),
  });
}

/** Save a manual step's tick with the run. */
export function useTickStep(run: string, runbook: string, step: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (done: boolean) => tickStep(run, runbook, step, done),
    // Returned, so the tick stays as chosen until the saved state has come back.
    onSettled: () => client.invalidateQueries({ queryKey: ["runbook-steps", run, runbook] }),
  });
}
