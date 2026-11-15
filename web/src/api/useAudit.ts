// Author: @SaadZarook
import { useInfiniteQuery } from "@tanstack/react-query";
import { fetchAudit, type AuditFilters } from "./client";

/** The audit, a page at a time, newest first, for the filters given. */
export function useAudit(filters: AuditFilters) {
  return useInfiniteQuery({
    queryKey: ["audit", filters],
    queryFn: ({ pageParam }) => fetchAudit(filters, pageParam),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (page) => page.next ?? undefined,
  });
}
