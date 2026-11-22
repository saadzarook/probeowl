// Author: @SaadZarook
import { useInfiniteQuery } from "@tanstack/react-query";
import { fetchRuns } from "./client";

/** Recent runs in one application and environment, a page at a time, newest first; drafts' test runs only if asked. */
export function useRuns(application: string | undefined, environment: string | undefined, drafts = false) {
  return useInfiniteQuery({
    queryKey: ["runs", application, environment, drafts],
    queryFn: ({ pageParam }) => fetchRuns(application ?? "", environment ?? "", pageParam, drafts),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => page.next ?? undefined,
    enabled: application !== undefined && environment !== undefined,
  });
}
