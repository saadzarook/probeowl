// Author: @SaadZarook
import { useInfiniteQuery } from "@tanstack/react-query";
import { fetchRuns } from "./client";

/** Recent runs in one application and environment, a page at a time, newest first. */
export function useRuns(application: string | undefined, environment: string | undefined) {
  return useInfiniteQuery({
    queryKey: ["runs", application, environment],
    queryFn: ({ pageParam }) => fetchRuns(application ?? "", environment ?? "", pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => page.next ?? undefined,
    enabled: application !== undefined && environment !== undefined,
  });
}
