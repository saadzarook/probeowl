// Author: @SaadZarook
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { confirmTable, fetchCatalog, fetchCatalogReview, fetchSchema, importSchema, type CatalogTable, type SchemaResult } from "./client";

/** The confirmed catalog of an application; data is null once loaded when it has none. */
export function useCatalog(application: string | undefined) {
  return useQuery({
    queryKey: ["catalog", application],
    queryFn: () => fetchCatalog(application ?? ""),
    enabled: application !== undefined,
  });
}

/** The latest imported schema of one connector in one environment. */
export function useSchema(application: string | undefined, connector: string | undefined, environment: string | undefined) {
  return useQuery({
    queryKey: ["schema", application, connector, environment],
    queryFn: () => fetchSchema(application ?? "", connector ?? "", environment ?? ""),
    enabled: application !== undefined && connector !== undefined && environment !== undefined,
  });
}

/** The command that imports a connector's schema in one environment. */
export function importCommand(application: string, connector: string, environment: string): string {
  return `owl catalog import ${connector} --application ${application} --env ${environment}`;
}

/** What to run to import a connector's schema, for when it has never been imported. */
export function notImported(application: string, connector: string, environment: string): string {
  return `${connector} has not been imported in ${environment}; run: ${importCommand(application, connector, environment)}`;
}

/**
 * The latest import of each connector in one environment, by connector name; absent until loaded.
 * `imported` holds the connectors the catalog review shows an import of here: the others are
 * never asked for, since the answer would be a 404, and get the notice saying how to import.
 */
export function useSchemas(application: string | undefined, connectors: string[], environment: string | undefined, imported: ReadonlySet<string>) {
  return useQueries({
    queries: connectors.map((connector) => ({
      queryKey: ["schema", application, connector, environment],
      queryFn: () => fetchSchema(application ?? "", connector, environment ?? ""),
      enabled: application !== undefined && environment !== undefined && imported.has(connector),
    })),
    combine: (results) => {
      const byConnector = new Map<string, SchemaResult>();
      results.forEach((r, i) => {
        const name = connectors[i];
        if (name === undefined) return;
        if (r.data) byConnector.set(name, r.data);
        else if (!imported.has(name) && application !== undefined && environment !== undefined) {
          byConnector.set(name, { notice: notImported(application, name, environment) });
        }
      });
      return byConnector;
    },
  });
}

/** Import from replica; on success the connector's schema query shows the new snapshot. */
export function useImportSchema(application: string, environment: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (connector: string) => importSchema(application, connector, environment),
    onSuccess: (snapshot) => {
      const result: SchemaResult = { snapshot };
      client.setQueryData(["schema", application, snapshot.connector, environment], result);
      void client.invalidateQueries({ queryKey: ["catalog-review", application] });
    },
  });
}

/** Suggestions, drift and usage for every table of an application; null once loaded when it has no catalog. */
export function useCatalogReview(application: string | undefined) {
  return useQuery({
    queryKey: ["catalog-review", application],
    queryFn: () => fetchCatalogReview(application ?? ""),
    enabled: application !== undefined,
  });
}

/** Preview a table entry (no base) or write it (with the preview's base); a write refreshes the catalog and the review. */
export function useConfirmTable(application: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ entry, base }: { entry: CatalogTable; base?: string }) => confirmTable(application, entry, base),
    onSuccess: async (change) => {
      if (!change.written) return;
      await Promise.all([
        client.invalidateQueries({ queryKey: ["catalog", application] }),
        client.invalidateQueries({ queryKey: ["catalog-review", application] }),
      ]);
    },
  });
}
