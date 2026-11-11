// Author: @SaadZarook
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchConnectors, testConnection } from "./client";

/** The backend keeps each reading for 60 s, so a reading is fresh for as long. */
const READING_MS = 60_000;

/** Connector status of one application in one environment; changing either refetches. */
export function useConnectors(application: string | undefined, environment: string | undefined) {
  return useQuery({
    queryKey: ["connectors", application, environment],
    queryFn: () => fetchConnectors(application ?? "", environment ?? ""),
    enabled: application !== undefined && environment !== undefined,
    staleTime: READING_MS,
    retry: false,
  });
}

/** Test one binding's connection; the backend drops its kept reading, so the status is read again. */
export function useTestConnection(application: string, connector: string, environment: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => testConnection(application, connector, environment),
    onSettled: () => void client.invalidateQueries({ queryKey: ["connectors", application, environment] }),
  });
}
