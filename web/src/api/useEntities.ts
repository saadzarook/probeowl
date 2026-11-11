// Author: @SaadZarook
import { useQuery } from "@tanstack/react-query";
import { fetchEntities, fetchEntity, fetchEntityYaml } from "./client";

/** The entities of an application, loaded through the generated client. */
export function useEntities(application: string | undefined) {
  return useQuery({
    queryKey: ["entities", application],
    queryFn: () => fetchEntities(application ?? ""),
    enabled: application !== undefined,
  });
}

/** An entity's file as it is on disk: what its tab edits and saves back. */
export function useEntityYaml(application: string | undefined, entity: string | undefined) {
  return useQuery({
    queryKey: ["entity-yaml", application, entity],
    queryFn: () => fetchEntityYaml(application ?? "", entity ?? ""),
    enabled: application !== undefined && entity !== undefined,
    retry: false,
  });
}

/** One entity of an application. */
export function useEntity(application: string | undefined, entity: string | undefined) {
  return useQuery({
    queryKey: ["entity", application, entity],
    queryFn: () => fetchEntity(application ?? "", entity ?? ""),
    enabled: application !== undefined && entity !== undefined,
  });
}
