// Author: @SaadZarook
import type { CatalogTable, FieldError } from "./api/client";
import type { DraftLocation } from "./entityDraft";
import { tableOf, type ImportedTable } from "./followKeys";

/** One failed save check, as the wizard shows it. */
export interface Failure {
  check: string;
  message: string;
}

/** The save checks' names the wizard knows a fix for, as the backend writes them. */
export const TIME_FILTER = "Time filter";
export const ROW_CEILING = "Row ceiling";
export const PII_MASKED = "PII masked";

/** A refused save's failures: by location when the check names one ("Time filter: cards"), else general. */
export function failuresByLocation(errors: FieldError[], locations: string[]): { byLocation: Map<string, Failure[]>; general: Failure[] } {
  const byLocation = new Map<string, Failure[]>();
  const general: Failure[] = [];
  for (const error of errors) {
    const split = error.field.lastIndexOf(": ");
    const location = split < 0 ? undefined : error.field.slice(split + 2);
    if (location !== undefined && locations.includes(location)) {
      byLocation.set(location, [...(byLocation.get(location) ?? []), { check: error.field.slice(0, split), message: error.message }]);
    } else {
      general.push({ check: error.field, message: error.message });
    }
  }
  return { byLocation, general };
}

/**
 * The catalog entry that fixes a location the time filter refuses, when the wizard followed a link to it:
 * its key column marked as a link to the column it holds. A Transaction, Log or Audit table may then be
 * read by that key, as the rule allows. Undefined when there is no such fix.
 */
export function linkFix(location: DraftLocation, failures: Failure[], catalog: CatalogTable[], tables: ImportedTable[]): LinkFix | undefined {
  const to = location.follows;
  if (to === undefined || !failures.some((f) => f.check === TIME_FILTER)) return undefined;
  const table = tableOf(location, tables)?.table;
  const entry = catalog.find((t) => t.connector === location.connector && t.table === table);
  if (entry === undefined) return undefined;
  const column = entry.columns[location.keyColumn];
  const roles = column?.roles.includes("link") ? column.roles : [...(column?.roles ?? []), "link" as const];
  return { entry: { ...entry, columns: { ...entry.columns, [location.keyColumn]: { ...column, roles, links_to: to } } }, column: location.keyColumn, to };
}

/** A catalog entry with one column marked as a link, and which link. */
export interface LinkFix {
  entry: CatalogTable;
  column: string;
  to: { connector: string; table: string; column: string };
}

/** The location with each selected column the catalog marks as PII masked: the fix for a PII check. */
export function masked(location: DraftLocation, catalog: CatalogTable[], tables: ImportedTable[]): DraftLocation {
  const table = tableOf(location, tables)?.table;
  const entry = catalog.find((t) => t.connector === location.connector && t.table === table);
  const pii = (column: string) => entry?.columns[column]?.roles.includes("pii") ?? false;
  return { ...location, columns: location.columns.map((c) => (pii(c.column) ? { ...c, pii: true } : c)) };
}
