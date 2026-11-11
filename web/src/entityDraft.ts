// Author: @SaadZarook
import { stringify } from "yaml";

/** A many location's row cap when nothing else is said: the usual 100, never above the row ceiling. */
export const DEFAULT_MAX_ROWS = 100;

/** One column a location selects: its name in the table, the field it exposes, and whether it is personal data. */
export interface DraftColumn {
  column: string;
  field: string;
  selected: boolean;
  pii: boolean;
}

/** One SQL location: a table on a connector, looked up by one of its columns. */
export interface DraftLocation {
  name: string;
  title: string;
  connector: string;
  /** As written in the lookup: the table, with its schema when it needs one. */
  table: string;
  /** The column the lookup filters by. */
  keyColumn: string;
  /** Where the key comes from: another location's field; undefined for the root key. */
  keyFrom?: { location: string; field: string };
  /** The table column its key column holds, when the wizard followed a link: what the catalog can record as that link. */
  follows?: { connector: string; table: string; column: string };
  cardinality: "one" | "many";
  maxRows?: number;
  columns: DraftColumn[];
}

/** One identifier: a field of a location that finds the subject, or the root key itself (no via). */
export interface DraftIdentifier {
  name: string;
  via?: { location: string; field: string };
  unique: boolean;
  pii: boolean;
  pattern: string;
}

/** Everything the wizard was told, before it is written. */
export interface EntityDraft {
  entity: string;
  title: string;
  owner: string;
  rootKey: string;
  locations: DraftLocation[];
  identifiers: DraftIdentifier[];
}

/** The parameter a location's lookup takes: the root key, or the field its key comes from. */
export function parameterOf(location: DraftLocation, rootKey: string): string {
  return location.keyFrom?.field ?? rootKey;
}

/** The single SELECT a location runs: its selected columns, by its key, as one parameter. */
export function lookupSql(location: DraftLocation, rootKey: string): string {
  const columns = location.columns.filter((c) => c.selected).map((c) => (c.field === c.column ? c.column : `${c.column} AS ${c.field}`));
  return `SELECT ${columns.join(", ")} FROM ${location.table} WHERE ${location.keyColumn} = :${parameterOf(location, rootKey)}`;
}

function locationDocument(location: DraftLocation, rootKey: string): Record<string, unknown> {
  const selected = location.columns.filter((c) => c.selected);
  const pii = selected.filter((c) => c.pii).map((c) => c.field);
  return {
    title: location.title,
    using: location.connector,
    ...(location.keyFrom ? { key_from: `${location.keyFrom.location}.${location.keyFrom.field}` } : {}),
    lookup: lookupSql(location, rootKey),
    ...(location.cardinality === "many" ? { cardinality: "many", max_rows: location.maxRows ?? DEFAULT_MAX_ROWS } : {}),
    exposes: selected.map((c) => c.field),
    ...(pii.length > 0 ? { pii } : {}),
  };
}

function identifierDocument(identifier: DraftIdentifier): Record<string, unknown> {
  return {
    ...(identifier.pattern.trim() ? { pattern: identifier.pattern.trim() } : {}),
    ...(identifier.via ? { via: `${identifier.via.location}.${identifier.via.field}` } : {}),
    unique: identifier.unique,
    ...(identifier.pii ? { pii: true } : {}),
  };
}

/** The title as given, or the id in words when it is left blank: visitor_profile is Visitor profile. */
export function entityTitle(draft: Pick<EntityDraft, "entity" | "title">): string {
  const given = draft.title.trim();
  if (given) return given;
  const words = draft.entity.replace(/_/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** The entity as the document its YAML file holds. */
export function entityDocument(draft: EntityDraft): Record<string, unknown> {
  return {
    entity: draft.entity,
    title: entityTitle(draft),
    root_key: draft.rootKey,
    owner: draft.owner,
    identifiers: Object.fromEntries(draft.identifiers.map((i) => [i.name, identifierDocument(i)])),
    locations: Object.fromEntries(draft.locations.map((l) => [l.name, locationDocument(l, draft.rootKey)])),
  };
}

/** The entity's YAML file, as the wizard writes it. */
export function entityYaml(draft: EntityDraft): string {
  const header = "# Written by the entity wizard. Edit freely; owl check <folder> checks it.\n";
  return header + stringify(entityDocument(draft), { lineWidth: 0 });
}

/** Problems that stop the draft before it is checked: the server checks everything else. */
export function draftProblems(draft: EntityDraft): string[] {
  const problems: string[] = [];
  const name = /^[a-z][a-z0-9_]*$/;
  if (!name.test(draft.entity)) problems.push("The entity id: a-z, 0-9 and _, starting with a letter.");
  if (!name.test(draft.rootKey)) problems.push("The root key: a-z, 0-9 and _, starting with a letter.");
  if (draft.owner.trim() === "") problems.push("Name the owning team.");
  if (draft.locations.length === 0) problems.push("Choose the root table.");
  for (const location of draft.locations) {
    if (!location.columns.some((c) => c.selected)) problems.push(`${location.name} selects no column.`);
  }
  return problems;
}
