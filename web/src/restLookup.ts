// Author: @SaadZarook
import type { CatalogTable, Run } from "./api/client";
import { DEFAULT_MAX_FANOUT, isRest, type DraftColumn, type DraftLocation } from "./entityDraft";
import { humanized, uniqueName } from "./followKeys";

// Follow a key to a REST API (#165). A REST location is a path under its connector's base URL, with
// the key as one more segment: ProbeOwl encodes it, so it can't add a segment or leave the base URL.
// The backend refuses everything below again before anything is sent; the form says so first.

// An encoded dot, slash or backslash would be a separator once decoded.
const ENCODED_SEPARATOR = /%(2e|2f|5c)/i;

/** Why a path can't be used, in words a person can act on; undefined when it can. */
export function restPathProblem(path: string): string | undefined {
  const typed = path.trim();
  if (typed === "") return "Type the path, such as /payments.";
  if (/^[a-z][a-z0-9+.-]*:/i.test(typed) || typed.startsWith("//")) return "Type a path under the connector's base URL, not a whole address.";
  if (!typed.startsWith("/")) return "Start the path with /: it goes under the connector's base URL.";
  if (typed.slice(1).includes("//")) return "// is refused: every segment needs a name.";
  if (/[?#\\]/.test(typed)) return "?, # and \\ are refused: the key is the only value, added as the last segment.";
  if (/\{|\}/.test(typed)) return "Leave out {{…}}: the key is added as the last segment.";
  if (/\s/.test(typed)) return "Spaces are refused in the path.";
  if (ENCODED_SEPARATOR.test(typed)) return "Encoded dots and slashes are refused.";
  const segments = typed.replace(/\/$/, "").split("/").slice(1);
  if (segments.some((s) => s === "." || s === "..")) return "Dot segments (. and ..) are refused: the lookup stays under the base URL.";
  return undefined;
}

/** The path as it is kept: trimmed, with no slash at the end. Only for a path with no problem. */
export function restPath(path: string): string {
  return path.trim().replace(/\/+$/, "");
}

/** A REST location keyed from a field of another location, or by the root key: one record until its answers say more. */
export function restLocation(
  connector: string,
  path: string,
  from: { location: DraftLocation; field: string } | undefined,
  rootKey: string,
  locations: DraftLocation[],
): DraftLocation {
  const taken = locations.map((l) => l.name);
  const segment = restPath(path).split("/").at(-1) || connector;
  // Named for the path, or for its API too when a table already has that name: cards, door_controller_cards.
  const name = uniqueName(taken.includes(segment) ? `${connector.replace(/_api$/, "")}_${segment}` : segment, taken);
  const byRootKey = from === undefined || from.field === rootKey;
  return {
    name,
    title: humanized(name),
    connector,
    table: "",
    keyColumn: "",
    path: restPath(path),
    ...(byRootKey ? {} : { keyFrom: { location: from.location.name, field: from.field } }),
    // A key with several values is looked up once for each, up to max_fanout.
    ...(from?.location.cardinality === "many" && !byRootKey ? { maxFanout: DEFAULT_MAX_FANOUT } : {}),
    cardinality: "one",
    columns: [],
  };
}

/**
 * Each REST location's fields as its answers in the run had them, added unselected to those it has.
 * The run shows a field it doesn't expose as [masked], but under its name: ticking one exposes it.
 * The same locations when nothing is new.
 */
export function withFieldsSeen(locations: DraftLocation[], run: Run): DraftLocation[] {
  let changed = false;
  const next = locations.map((location) => {
    if (!isRest(location)) return location;
    const seen = new Set<string>();
    for (const result of run.locations ?? []) {
      if (result.location !== location.name) continue;
      for (const row of result.evidence?.rows ?? []) for (const key of Object.keys(row)) seen.add(key);
    }
    const added = [...seen].filter((name) => !location.columns.some((c) => c.field === name)).sort();
    if (added.length === 0) return location;
    changed = true;
    const columns: DraftColumn[] = added.map((name) => ({ column: name, field: name, selected: false, pii: false }));
    return { ...location, columns: [...location.columns, ...columns] };
  });
  return changed ? next : locations;
}

/** A REST lookup the selected location's catalog offers: a column marked as a key of a REST connector. */
export interface RestLink {
  from: { location: string; column: string };
  connector: string;
}

/** The catalog's links from the location's columns to a REST connector: an external key, such as provider_payment_id. */
export function restLinks(location: DraftLocation, catalog: CatalogTable[], restConnectors: string[]): RestLink[] {
  if (isRest(location) || location.maxFanout !== undefined) return [];
  const table = location.table.split(".").at(-1) ?? location.table;
  const entry = catalog.find((t) => t.connector === location.connector && t.table.toLowerCase() === table.toLowerCase());
  return location.columns.flatMap((c) => {
    const target = entry?.columns?.[c.column]?.links_to;
    if (!target || !restConnectors.includes(target.connector)) return [];
    return [{ from: { location: location.name, column: c.column }, connector: target.connector }];
  });
}

/** The REST paths of the draft, each as connector and lookup: what Check sample sends for the first time. */
export function restLookups(locations: DraftLocation[]): string[] {
  return locations.filter(isRest).map((l) => `${l.connector} ${l.path ?? ""}`);
}
