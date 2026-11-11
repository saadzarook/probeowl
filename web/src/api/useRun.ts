// Author: @SaadZarook
import { useQuery } from "@tanstack/react-query";
import { fetchRun } from "./client";

export const RUN_POLL_MS = 1000;

/** Poll a run every second until it is no longer running; locations fill in as they arrive. */
export function useRun(id: string | undefined) {
  return useQuery({
    queryKey: ["run", id],
    queryFn: () => fetchRun(id ?? ""),
    enabled: id !== undefined,
    refetchInterval: (query) => (query.state.data?.status === "running" ? RUN_POLL_MS : false),
  });
}
