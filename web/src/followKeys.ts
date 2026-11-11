// Author: @SaadZarook
import type { CatalogTable, SchemaSnapshot } from "./api/client";
import { DEFAULT_MAX_ROWS, type DraftColumn, type DraftIdentifier, type DraftLocation } from "./entityDraft";

/** One imported table of a SQL connector in the chosen environment. */
export interface ImportedTable {
  connector: string;
  schema: string | null;
  table: string;
  columns: string[];
  primaryKey: string[];
  /** Each unique index's columns. */
  unique: string[][];
}

/** How a suggested link was found: what makes it more than a guess. */
export type LinkReason = "catalog" | "same name" | "table key" | "chosen";

/** A table to add as a location, looked up by one of its columns, keyed from an added location's column. */
export interface Link {
  from: { location: string; column: string };
  to: { connector: string; table: string; column: string };
  reason: LinkReason;
}

// Names too common to mean two columns hold the same thing: offered only when the catalog says so.
const GENERIC = new Set([
  "id", "status", "type", "name", "code", "state", "kind", "title", "description", "value",
  "amount", "currency", "version", "created", "updated", "modified", "created_at", "updated_at",
  "deleted_at", "created_by", "updated_by", "date", "time", "timestamp", "active", "enabled",
  "deleted", "flag", "note", "notes", "comment", "comments", "label", "category", "level",
]); // prettier-ignore

/** A column name too common to suggest a link on by name alone. */
export function isGeneric(column: string): boolean {
  const name = column.toLowerCase();
  return GENERIC.has(name) || /_(at|on|date|time|ts)$/.test(name) || /^(is|has)_/.test(name);
}

/** The tables of each snapshot, as the wizard uses them. */
export function importedTables(snapshots: SchemaSnapshot[]): ImportedTable[] {
  return snapshots.flatMap((s) =>
    s.tables.map((t) => ({
      connector: s.connector,
      schema: t.schema ?? null,
      table: t.table,
      columns: t.columns.map((c) => c.name),
      primaryKey: t.primary_key ?? [],
      unique: (t.indexes ?? []).filter((i) => i.unique).map((i) => i.columns),
    })),
  );
}

/** The table's name as the lookup writes it: with its schema only when that is not its connector's usual one. */
export function tableReference(table: ImportedTable, all: ImportedTable[]): string {
  if (table.schema === null) return table.table;
  const counts = new Map<string, number>();
  for (const t of all) if (t.connector === table.connector && t.schema !== null) counts.set(t.schema, (counts.get(t.schema) ?? 0) + 1);
  const usual = [...counts.entries()].sort((a, b) => b[1] - a[1])[0]?.[0];
  return table.schema === usual ? table.table : `${table.schema}.${table.table}`;
}

/** Whether one row at most has this value: the column alone is the primary key or a unique index. */
export function isUnique(table: ImportedTable, column: string): boolean {
  return uniqueBecause(table, column) !== undefined;
}

/** Why the column is unique, in words; undefined when it is not. */
export function uniqueBecause(table: ImportedTable, column: string): string | undefined {
  const alone = (columns: string[]) => columns.length === 1 && columns[0] === column;
  if (alone(table.primaryKey)) return `${possessive(table.table)} primary key`;
  return table.unique.some(alone) ? `a unique index on ${table.table}` : undefined;
}

/** The imported table a location reads, found by its connector and the name its lookup writes. */
export function tableOf(location: DraftLocation, tables: ImportedTable[]): ImportedTable | undefined {
  return tables.find((t) => t.connector === location.connector && tableReference(t, tables) === location.table);
}

function singular(name: string): string {
  if (name.endsWith("ies")) return `${name.slice(0, -3)}y`;
  return name.endsWith("s") && !name.endsWith("ss") ? name.slice(0, -1) : name;
}

function findTable(tables: ImportedTable[], connector: string, table: string): ImportedTable | undefined {
  return tables.find((t) => t.connector === connector && t.table === table);
}

function catalogLink(catalog: CatalogTable[], connector: string, table: string, column: string) {
  return catalog.find((t) => t.connector === connector && t.table === table)?.columns?.[column]?.links_to ?? undefined;
}

/** Tables to add next, keyed from a column of an added location: confirmed by the catalog, or by a distinctive name. */
export function suggestedLinks(locations: DraftLocation[], tables: ImportedTable[], catalog: CatalogTable[]): Link[] {
  const found = new Map<string, Link>();
  const added = new Set(locations.map((l) => `${l.connector}/${l.table}`));
  const offer = (link: Link) => {
    const key = `${link.from.location}.${link.from.column}>${link.to.connector}/${link.to.table}.${link.to.column}`;
    if (!found.has(key) && !added.has(`${link.to.connector}/${link.to.table}`)) found.set(key, link);
  };
  for (const location of locations) {
    const source = findTable(tables, location.connector, location.table);
    if (!source) continue;
    for (const column of source.columns) {
      // The catalog says where this column points, on any connector.
      const target = catalogLink(catalog, source.connector, source.table, column);
      if (target?.table && target.column) offer({ from: { location: location.name, column }, to: { connector: target.connector, table: target.table, column: target.column }, reason: "catalog" });
    }
    for (const other of tables) {
      if (other.connector === source.connector && other.table === source.table) continue;
      for (const column of other.columns) {
        const back = catalogLink(catalog, other.connector, other.table, column);
        if (back?.connector === source.connector && back.table === source.table && back.column && source.columns.includes(back.column)) {
          offer({ from: { location: location.name, column: back.column }, to: { connector: other.connector, table: other.table, column }, reason: "catalog" });
        } else if (!isGeneric(column) && source.columns.includes(column)) {
          offer({ from: { location: location.name, column }, to: { connector: other.connector, table: other.table, column }, reason: "same name" });
        } else if (source.primaryKey.length === 1) {
          // cards.visitor_profile_id for visitor_profile.id: the name says which table's key it holds.
          const key = source.primaryKey[0] ?? "";
          const names = [`${source.table}_${key}`, `${singular(source.table)}_${key}`];
          if (names.includes(column.toLowerCase())) offer({ from: { location: location.name, column: key }, to: { connector: other.connector, table: other.table, column }, reason: "table key" });
        }
      }
    }
  }
  return [...found.values()];
}

/** A location name for a table, not yet taken. */
export function uniqueName(table: string, taken: string[]): string {
  const base = table.toLowerCase().replace(/[^a-z0-9_]/g, "_").replace(/^[^a-z]+/, "") || "location";
  let name = base;
  for (let n = 2; taken.includes(name); n++) name = `${base}_${n}`;
  return name;
}

/** A title for a table: its name in words. */
export function humanized(name: string): string {
  const words = name.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** The columns a new location selects at first: all of them, personal data marked from the catalog. */
export function draftColumns(table: ImportedTable, catalog: CatalogTable[]): DraftColumn[] {
  const entry = catalog.find((t) => t.connector === table.connector && t.table === table.table);
  return table.columns.map((column) => ({
    column,
    field: column,
    selected: true,
    pii: entry?.columns?.[column]?.roles.includes("pii") ?? false,
  }));
}

/** The location a link adds: one row when the column is unique, else many, capped at 100 or the row ceiling. */
export function linkedLocation(
  link: Link,
  from: DraftLocation,
  rootKey: string,
  tables: ImportedTable[],
  catalog: CatalogTable[],
  locations: DraftLocation[],
  rowCeiling: number | undefined,
): DraftLocation | undefined {
  const table = findTable(tables, link.to.connector, link.to.table);
  const fromColumn = from.columns.find((c) => c.column === link.from.column);
  if (!table || !fromColumn) return undefined;
  const one = isUnique(table, link.to.column);
  const name = uniqueName(table.table, locations.map((l) => l.name));
  const byRootKey = fromColumn.field === rootKey;
  const columns = draftColumns(table, catalog);
  const source = tableOf(from, tables);
  return {
    name,
    title: humanized(table.table),
    connector: table.connector,
    table: tableReference(table, tables),
    keyColumn: link.to.column,
    ...(source ? { follows: { connector: source.connector, table: source.table, column: link.from.column } } : {}),
    // Keyed by the root key itself, it needs no key_from: the root key is every lookup's default.
    ...(byRootKey ? {} : { keyFrom: { location: from.name, field: fromColumn.field } }),
    cardinality: one ? "one" : "many",
    ...(one ? {} : { maxRows: Math.min(DEFAULT_MAX_ROWS, rowCeiling ?? DEFAULT_MAX_ROWS) }),
    // Keyed by the root key, it exposes its key column as the root key, so it can find a subject too.
    columns: byRootKey ? columns.map((c) => (c.column === link.to.column ? { ...c, field: rootKey } : c)) : columns,
  };
}

/** Why a link is offered, in words. */
export function reasonText(reason: LinkReason): string {
  return {
    catalog: "the catalog links these columns",
    "same name": "same column name",
    "table key": "named for that table's key",
    chosen: "chosen by you",
  }[reason];
}

/** The root key's name: the root table's key column, or <entity>_id when it is just id. */
export function defaultRootKey(entity: string, table: ImportedTable): string {
  const key = table.primaryKey[0] ?? table.columns[0] ?? "id";
  return isGeneric(key) ? `${entity || "subject"}_id` : key;
}

/** The first location: the root table, looked up by its primary key, which it exposes as the root key. */
export function rootLocation(table: ImportedTable, tables: ImportedTable[], catalog: CatalogTable[], rootKey: string): DraftLocation {
  const key = table.primaryKey[0] ?? table.columns[0] ?? "id";
  return {
    name: uniqueName(table.table, []),
    title: humanized(table.table),
    connector: table.connector,
    table: tableReference(table, tables),
    keyColumn: key,
    cardinality: "one",
    columns: draftColumns(table, catalog).map((c) => (c.column === key ? { ...c, field: rootKey } : c)),
  };
}

/**
 * Each selected field that could also find the subject, with what the schema and catalog say about it.
 * Only a location that exposes the root key can: it is how a value found there leads to its subject.
 */
export function identifierCandidates(locations: DraftLocation[], tables: ImportedTable[], rootKey: string): IdentifierCandidate[] {
  const exposesRootKey = (l: DraftLocation) => l.columns.some((c) => c.selected && c.field === rootKey);
  return locations.filter(exposesRootKey).flatMap((location) => {
    const table = tableOf(location, tables);
    return location.columns
      .filter((c) => c.selected && c.field !== rootKey)
      .map((c) => {
        // Unique when the column is unique in its table, even in a many location: one card ID, one card, one visitor.
        const because = table && uniqueBecause(table, c.column);
        return { location: location.name, field: c.field, unique: because !== undefined, pii: c.pii, ...(because ? { because } : {}) };
      });
  });
}

/** A field that could find the subject, with why it is unique when it is. */
export interface IdentifierCandidate {
  location: string;
  field: string;
  unique: boolean;
  pii: boolean;
  because?: string;
}

/**
 * The identifiers a newly added location brings, ticked for the person: its unique columns that are
 * not personal data. A unique PII column, such as an email, stays offered but unticked.
 */
export function pretickedIdentifiers(added: DraftLocation[], tables: ImportedTable[], rootKey: string, existing: DraftIdentifier[]): DraftIdentifier[] {
  const chosen = [...existing];
  for (const c of identifierCandidates(added, tables, rootKey)) {
    if (!c.unique || c.pii || chosen.some((i) => i.via?.location === c.location && i.via.field === c.field)) continue;
    chosen.push({ name: freeIdentifierName(c, rootKey, chosen), via: { location: c.location, field: c.field }, unique: true, pii: false, pattern: "" });
  }
  return chosen.slice(existing.length);
}

/** The field's name, or location_field when another identifier already has it. */
export function freeIdentifierName(c: { location: string; field: string }, rootKey: string, others: DraftIdentifier[]): string {
  const taken = new Set([rootKey, ...others.map((i) => i.name)]);
  return taken.has(c.field) ? `${c.location}_${c.field}` : c.field;
}

/** A table's name as an owner: cards' key, visitor_profile's key. */
function possessive(name: string): string {
  return name.endsWith("s") ? `${name}'` : `${name}'s`;
}
