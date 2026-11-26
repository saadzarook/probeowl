// Author: @SaadZarook
import { stringify } from "yaml";

/** A many location's row cap when nothing else is said: the usual 100, never above the row ceiling. */
export const DEFAULT_MAX_ROWS = 100;

/** The most rows a fan-out is repeated for when nothing else is said. */
export const DEFAULT_MAX_FANOUT = 10;

/** One column a location selects: its name in the table, the field it exposes, and whether it is personal data. */
export interface DraftColumn {
  column: string;
  field: string;
  selected: boolean;
  pii: boolean;
}

/**
 * One location: a table on a SQL connector, looked up by one of its columns, or a path on a REST
 * connector, looked up with its key as the last segment.
 */
export interface DraftLocation {
  name: string;
  title: string;
  connector: string;
  /** As written in the lookup: the table, with its schema when it needs one. Empty for REST. */
  table: string;
  /** The column the lookup filters by. Empty for REST. */
  keyColumn: string;
  /** REST: the path under the connector's base URL, before the key, such as /payments. */
  path?: string;
  /** Where the key comes from: another location's field; undefined for the root key. */
  keyFrom?: { location: string; field: string };
  /** The table column its key column holds, when the wizard followed a link: what the catalog can record as that link. */
  follows?: { connector: string; table: string; column: string };
  cardinality: "one" | "many";
  maxRows?: number;
  /** A fan-out: looked up again for each row of the many location its key comes from, up to this many. */
  maxFanout?: number;
  /** SQL: the columns it can select. REST: the fields its answers had, selected when exposed. */
  columns: DraftColumn[];
}

/** Whether the location reads a REST API. */
export function isRest(location: DraftLocation): boolean {
  return location.path !== undefined;
}

/** A location's name as the save checks say it: a fan-out's is under the location it fans out from. */
export function checkedName(location: DraftLocation): string {
  return location.maxFanout !== undefined && location.keyFrom ? `${location.keyFrom.location}.${location.name}` : location.name;
}

/**
 * The locations with each one keyed from a many location made a fan-out, and no other: a key with
 * several values would leave an ordinary location Blocked. Changing a location to many, or back,
 * changes those keyed from it.
 */
export function withFanOuts(locations: DraftLocation[]): DraftLocation[] {
  return locations.map((location) => {
    const from = locations.find((l) => l.name === location.keyFrom?.location);
    const { maxFanout, ...rest } = location;
    if (from?.cardinality !== "many") return maxFanout === undefined ? location : rest;
    return maxFanout === undefined ? { ...location, maxFanout: DEFAULT_MAX_FANOUT } : location;
  });
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

/** A REST location's lookup: its path, then its key as one more segment, which ProbeOwl encodes. */
export function restLookup(location: DraftLocation, rootKey: string): string {
  return `GET ${location.path ?? ""}/{{${parameterOf(location, rootKey)}}}`;
}

/** What a location reads, in a few words: its table, or its REST lookup. */
export function sourceText(location: DraftLocation, rootKey: string): string {
  return isRest(location) ? restLookup(location, rootKey) : location.table;
}

/** The single SELECT a location runs: its selected columns, by its key, as one parameter. */
export function lookupSql(location: DraftLocation, rootKey: string): string {
  const columns = location.columns.filter((c) => c.selected).map((c) => (c.field === c.column ? c.column : `${c.column} AS ${c.field}`));
  return `SELECT ${columns.join(", ")} FROM ${location.table} WHERE ${location.keyColumn} = :${parameterOf(location, rootKey)}`;
}

function locationDocument(location: DraftLocation, rootKey: string, all: DraftLocation[]): Record<string, unknown> {
  const selected = location.columns.filter((c) => c.selected);
  const pii = selected.filter((c) => c.pii).map((c) => c.field);
  const fanOut = location.maxFanout !== undefined;
  const each = all.filter((l) => l.maxFanout !== undefined && l.keyFrom?.location === location.name);
  return {
    title: location.title,
    using: location.connector,
    // A fan-out's key is its parent row's field, so it needs no key_from.
    ...(location.keyFrom && !fanOut ? { key_from: `${location.keyFrom.location}.${location.keyFrom.field}` } : {}),
    lookup: isRest(location) ? restLookup(location, rootKey) : lookupSql(location, rootKey),
    ...(location.cardinality === "many" ? { cardinality: "many", ...(fanOut ? {} : { max_rows: location.maxRows ?? DEFAULT_MAX_ROWS }) } : {}),
    ...(fanOut ? { max_fanout: location.maxFanout } : {}),
    // A REST location exposes nothing until a field is ticked: everything it answers is masked.
    ...(selected.length > 0 || !isRest(location) ? { exposes: selected.map((c) => c.field) } : {}),
    ...(pii.length > 0 ? { pii } : {}),
    ...(each.length > 0 ? { each: Object.fromEntries(each.map((l) => [l.name, locationDocument(l, rootKey, all)])) } : {}),
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
    locations: Object.fromEntries(draft.locations.filter((l) => l.maxFanout === undefined).map((l) => [l.name, locationDocument(l, draft.rootKey, draft.locations)])),
  };
}

/** The entity's YAML file, as the entity graph writes it. */
export function entityYaml(draft: EntityDraft): string {
  const header = "# Written by the entity graph. Edit freely; owl check <folder> checks it.\n";
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
    // A REST location may expose nothing yet: its first lookup shows what it can.
    if (!isRest(location) && !location.columns.some((c) => c.selected)) problems.push(`${location.name} selects no column.`);
    const from = draft.locations.find((l) => l.name === location.keyFrom?.location);
    if (from?.maxFanout !== undefined) problems.push(`${location.name} is keyed from ${from.name}, which is looked up for each row: a fan-out can't have another.`);
  }
  return problems;
}
