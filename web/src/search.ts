// Author: @SaadZarook
import type { ApplicationSummary, EnvironmentSummary, SearchItem } from "./api/client";

// Global search's links, environment choice and recent items (#38). Every path is fixed here and
// built with encodeURIComponent, as docs/decisions.md asks until #34.

const at = (path: string, params: Record<string, string> = {}): string => {
  const query = Object.entries(params)
    .map(([key, value]) => `${key}=${encodeURIComponent(value)}`)
    .join("&");
  return query ? `${path}?${query}` : path;
};

/** The screen a result opens, in its own application. */
export function resultPath(item: Pick<SearchItem, "kind" | "id">): string {
  switch (item.kind) {
    case "application":
      return "/inspect";
    case "entity":
      return at("/probes", { entity: item.id });
    case "location":
      // entity.location: the entity's probes, where its locations are.
      return at("/probes", { entity: item.id.split(".")[0] ?? item.id });
    case "probe":
      return at("/probes", { probe: item.id });
    case "connector":
      return at("/connectors", { connector: item.id });
    case "runbook":
      return at("/knowledge-base", { runbook: item.id });
    case "table":
      return at("/tables", { table: item.id });
    case "column":
      // connector.table.column: its table.
      return at("/tables", { table: item.id.split(".").slice(0, 2).join(".") });
    case "run":
      return at("/runs", { run: item.id });
  }
}

/** Inspect or Validate with the value typed in the palette, filled in and not yet run. */
export const withValue = (screen: "/inspect" | "/validate", value: string): string => at(screen, { value });

export interface EnvironmentChoice {
  environment: EnvironmentSummary | undefined;
  /** What to tell the person when the environment shown changes; never silent. */
  notice: string | undefined;
}

/**
 * The environment to open another application in: the one shown now if it has it, else its first
 * that isn't Production. Only an application with nothing but Production opens there, and says so.
 */
export function environmentFor(target: ApplicationSummary, current: EnvironmentSummary | undefined): EnvironmentChoice {
  const same = target.environments.find((e) => e.name === current?.name);
  if (same) return { environment: same, notice: undefined };
  const safe = target.environments.find((e) => !e.production);
  const chosen = safe ?? target.environments[0];
  if (chosen === undefined) return { environment: undefined, notice: undefined };
  const had = current ? `${target.title} has no ${current.title}: ` : "";
  return { environment: chosen, notice: `${had}showing ${target.title} ${chosen.title}${chosen.production ? ", which is Production" : ""}.` };
}

// --- Recent items: kinds and ids only, per viewer, never a run's subject ----------------------

const RECENT_KEY = "probeowl-search-recent";
const RECENT_MAX = 8;

export interface RecentItem {
  kind: SearchItem["kind"];
  application: string | null;
  id: string;
  /** Shown in the list; a run's is "Run <id>", never its subject. */
  title: string;
}

export function readRecent(): RecentItem[] {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(RECENT_KEY) ?? "[]");
    return Array.isArray(parsed) ? (parsed as RecentItem[]).slice(0, RECENT_MAX) : [];
  } catch {
    return []; // storage blocked or unreadable: no recent items, nothing else changes
  }
}

export function rememberRecent(item: Pick<SearchItem, "kind" | "application" | "id" | "title">): void {
  const kept: RecentItem = {
    kind: item.kind,
    application: item.application ?? null,
    id: item.id,
    title: item.kind === "run" ? `Run ${item.id.slice(0, 8)}` : item.title,
  };
  const others = readRecent().filter((r) => !(r.kind === kept.kind && r.id === kept.id && r.application === kept.application));
  try {
    localStorage.setItem(RECENT_KEY, JSON.stringify([kept, ...others].slice(0, RECENT_MAX)));
  } catch {
    // storage blocked: the palette still works, without recent items
  }
}

// --- The bundled guide's own search index --------------------------------------------------

export interface GuidePage {
  location: string;
  title: string;
}

/** Guide pages and sections whose title has the query, at most five, from MkDocs' index. */
export function guideMatches(index: GuidePage[], q: string): GuidePage[] {
  const folded = q.trim().toLowerCase();
  if (folded === "") return [];
  return index.filter((page) => page.title.toLowerCase().includes(folded)).slice(0, 5);
}
