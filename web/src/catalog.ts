// Author: @SaadZarook
import type { CatalogReview, CatalogTable, ColumnRole, LabelSuggestion, LinkTarget, TableLabel, TableReview, TableSchema } from "./api/client";

export const LABELS: { label: TableLabel; title: string; sets: string }[] = [
  // What confirming each label changes, as the save checks apply it (backend/probeowl/checks).
  { label: "transaction", title: "Transaction", sets: "Rows pile up over time. Needs a time column, and every lookup on it must use a key or a time window from a parameter, or saving refuses it." },
  { label: "master", title: "Master", sets: "One current record per subject, looked up by its key. No extra rule beyond the index check every lookup gets." },
  { label: "audit", title: "Audit", sets: "A history of changes. Needs a time column, and every lookup on it must use a key or a time window from a parameter, or saving refuses it." },
  { label: "log", title: "Log", sets: "Events kept for a while. Needs a time column, and every lookup on it must use a key or a time window from a parameter, or saving refuses it." },
  { label: "reference", title: "Reference", sets: "A small, slow-changing list. A full scan of it passes the index check." },
  { label: "external_mapping", title: "External mapping", sets: "Holds another system's key, for key_from to follow. No extra rule." },
  { label: "outbox", title: "Outbox", sets: "Messages waiting to be published. No extra rule in v0.1." },
];

export const ROLE_TITLES = {
  primary_key: "Primary key",
  time: "Time",
  status: "Status",
  link: "Link",
  external_key: "External key",
  pii: "PII",
  compare: "Compare",
  expected_to_vary: "Expected to vary",
} as const;

export const ROLES = Object.keys(ROLE_TITLES) as ColumnRole[];

/** Dialect of each connector in examples/; the API has no connectors route yet, so unknown connectors show none. */
const DIALECTS: Record<string, string> = { wallet_db: "SQL Server", ledger_db: "Oracle", access_db: "MariaDB" };

export const dialectOf = (connector: string): string | undefined => DIALECTS[connector];

export const labelTitle = (label: TableLabel): string => LABELS.find((l) => l.label === label)?.title ?? label;

/** Table names are lower-case in the catalog but may be upper-case in an Oracle import. */
export const sameName = (a: string, b: string): boolean => a.toLowerCase() === b.toLowerCase();

/** Find a table's imported definition. */
export function importedTable(table: { table: string }, tables: TableSchema[] | undefined): TableSchema | undefined {
  return tables?.find((t) => sameName(t.table, table.table));
}

/** A table of the list: confirmed in catalog.yaml, or only suggested from an import or a probe. */
export interface TableRow {
  connector: string;
  table: string;
  confirmed: CatalogTable | undefined;
  suggestion: LabelSuggestion | null;
  review: TableReview | undefined;
}

export const rowKey = (row: { connector: string; table: string }): string => `${row.connector}.${row.table}`;

/** The confirmed catalog tables first, then the reviewed tables that are not in it. */
export function buildRows(tables: CatalogTable[], review: CatalogReview | null | undefined): TableRow[] {
  const reviews = review?.tables ?? [];
  const find = (t: { connector: string; table: string }) => reviews.find((r) => r.connector === t.connector && sameName(r.table, t.table));
  const confirmed = tables.map((t) => ({ connector: t.connector, table: t.table, confirmed: t, suggestion: find(t)?.suggestion ?? null, review: find(t) }));
  const suggested = reviews
    .filter((r) => !tables.some((t) => t.connector === r.connector && sameName(t.table, r.table)))
    .map((r) => ({ connector: r.connector, table: r.table, confirmed: undefined, suggestion: r.suggestion, review: r }));
  return [...confirmed, ...suggested];
}

/** The confirmed label, else the suggested one. */
export const effectiveLabel = (row: TableRow): TableLabel | undefined => row.confirmed?.label ?? row.suggestion?.label;

/** Marked columns of the confirmed entry, else the suggestion's roles. */
export function startingColumns(row: TableRow): CatalogTable["columns"] {
  if (row.confirmed) return row.confirmed.columns;
  const suggested = Object.entries(row.suggestion?.columns ?? {}).filter(([, roles]) => roles.length > 0);
  // A suggested link comes with where it points, such as visitor_profile_id to visitor_profile.id.
  const links = row.suggestion?.links ?? {};
  return Object.fromEntries(suggested.map(([name, roles]) => [name, { roles, ...(links[name] ? { links_to: links[name] } : {}) }]));
}

/** "2 probes", "1 probe" or "none". */
export const usedByText = (probes: string[] | undefined): string =>
  !probes || probes.length === 0 ? "none" : `${probes.length} ${probes.length === 1 ? "probe" : "probes"}`;

/** "connector.table.column" as a link target; the table and column are optional (an external key has only a connector). */
export function parseTarget(text: string): LinkTarget | undefined {
  const [connector, table, column] = text.trim().split(".");
  if (!connector) return undefined;
  return { connector, ...(table ? { table } : {}), ...(column ? { column } : {}) };
}

export function formatTarget(links: LinkTarget | null | undefined): string {
  return links ? [links.connector, links.table, links.column].filter(Boolean).join(".") : "";
}

/** "~48 M" for an estimate; small counts are shown as they are. */
export function formatRows(rows: number | null | undefined): string {
  if (rows === null || rows === undefined) return "—";
  if (rows < 1000) return String(rows);
  const units: [number, string][] = [[1e9, "B"], [1e6, "M"], [1e3, "K"]];
  const [size, unit] = units.find(([s]) => rows >= s) ?? [1, ""];
  const value = rows / size;
  return `~${value < 10 ? value.toFixed(1).replace(/\.0$/, "") : Math.round(value)} ${unit}`;
}

/** "30d" as "30 d". */
export const formatRetention = (retention: string): string => retention.replace(/^(\d+)/, "$1 ");

/** The time column of a table, with its retention for Log tables; "—" when it has none. */
export function timeColumn(table: { label?: TableLabel | undefined; retention?: string | null | undefined; columns: CatalogTable["columns"] }): string {
  const name = Object.entries(table.columns).find(([, c]) => c.roles.includes("time"))?.[0];
  if (name === undefined) return "—";
  return table.label === "log" && table.retention ? `${name} · ${formatRetention(table.retention)}` : name;
}

export interface MergedColumn {
  name: string;
  type: string | undefined;
}

/** Imported columns in database order; marked columns missing from the import follow. */
export function mergeColumns(marked: string[], imported: TableSchema | undefined): MergedColumn[] {
  if (imported === undefined) return marked.map((name) => ({ name, type: undefined }));
  const fromImport = imported.columns.map((c) => ({ name: c.name, type: c.type }));
  const extra = marked.filter((name) => !imported.columns.some((c) => sameName(c.name, name))).map((name) => ({ name, type: undefined }));
  return [...fromImport, ...extra];
}

export interface ColumnDraft {
  roles: ColumnRole[];
  indexed: boolean;
  values: string;
  target: string;
}

export interface Draft {
  label: TableLabel | "";
  owner: string;
  description: string;
  columns: Record<string, ColumnDraft>;
}

export const NO_MARKS: ColumnDraft = { roles: [], indexed: false, values: "", target: "" };

/** Start from the confirmed entry, else the suggestion; the owner starts from the application's. */
export function startingDraft(row: TableRow, applicationOwner: string): Draft {
  const columns = Object.fromEntries(
    Object.entries(startingColumns(row)).map(([name, c]) => [
      name,
      { roles: c.roles, indexed: c.indexed === true, values: (c.values ?? []).join(", "), target: formatTarget(c.links_to) },
    ]),
  );
  return {
    label: row.confirmed?.label ?? row.suggestion?.label ?? "",
    owner: row.confirmed?.owner ?? applicationOwner,
    description: row.confirmed?.description ?? "",
    columns,
  };
}

/** The entry to send; a column is written only with the fields its roles use. Undefined until a label is chosen. */
export function toEntry(row: TableRow, draft: Draft): CatalogTable | undefined {
  if (draft.label === "") return undefined;
  const columns: CatalogTable["columns"] = {};
  for (const [name, c] of Object.entries(draft.columns)) {
    if (c.roles.length === 0) continue;
    const values = c.values.split(",").map((v) => v.trim()).filter(Boolean);
    const links = c.roles.includes("link") || c.roles.includes("external_key") ? parseTarget(c.target) : undefined;
    columns[name] = {
      roles: c.roles,
      ...(c.roles.includes("time") && c.indexed ? { indexed: true } : {}),
      ...(c.roles.includes("status") && values.length > 0 ? { values } : {}),
      ...(links ? { links_to: links } : {}),
    };
  }
  const description = draft.description.trim();
  return {
    connector: row.connector,
    table: row.table,
    label: draft.label,
    owner: draft.owner.trim(),
    ...(description ? { description } : {}),
    ...(row.confirmed?.retention ? { retention: row.confirmed.retention } : {}),
    ...(row.confirmed?.approx_rows != null ? { approx_rows: row.confirmed.approx_rows } : {}),
    columns,
  };
}

/** A table's entry as its import suggests it, unchanged: what Confirm all writes. Undefined without a suggested label. */
export function suggestedEntry(row: TableRow, applicationOwner: string): CatalogTable | undefined {
  return toEntry(row, startingDraft(row, applicationOwner));
}
