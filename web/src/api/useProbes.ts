// Author: @SaadZarook
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchProbes, saveProbe, type SaveProbe } from "./client";

/** The probes of one entity, loaded through the generated client. */
export function useProbes(application: string | undefined, entity: string | undefined) {
  return useQuery({
    queryKey: ["probes", application, entity],
    queryFn: () => fetchProbes(application ?? "", entity ?? ""),
    enabled: application !== undefined && entity !== undefined,
  });
}

/** Every probe of an application. */
export function useApplicationProbes(application: string | undefined) {
  return useQuery({
    queryKey: ["probes", application],
    queryFn: () => fetchProbes(application ?? ""),
    enabled: application !== undefined,
  });
}

/** Preview or write a probe save; a write refreshes the probes and entities of the application. */
export function useSaveProbe(application: string, probe: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: SaveProbe) => saveProbe(application, probe, body),
    onSuccess: (change) => {
      if (!change.written) return;
      void client.invalidateQueries({ queryKey: ["probes", application] });
      void client.invalidateQueries({ queryKey: ["entities", application] });
      void client.invalidateQueries({ queryKey: ["entity", application] });
      void client.invalidateQueries({ queryKey: ["entity-yaml", application] });
    },
  });
}
