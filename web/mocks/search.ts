// Author: @SaadZarook
import type { SearchGroup, SearchItem, SearchKind, SearchResults } from "../src/api/client";

// GET /api/search over the mocks' own data, by the backend's rules (backend/probeowl/search.py):
// prefix before substring, names before titles; runs by an id prefix of 4 or more characters, or
// by their subject or identifier exactly, or by a prefix when it isn't masked. "[masked]" never
// matches. Imported tables and columns are left to the backend's tests.

export interface Searchable {
  kind: Exclude<SearchKind, "run">;
  application: string | null;
  id: string;
  title: string;
  detail?: string | null;
  labels?: string[];
  /** Matched after the name and title, as a runbook's symptoms are. */
  also?: string[];
}

export interface SearchableRun {
  id: string;
  application: string;
  subject?: string | null;
  identifier_value: string;
  status: string;
  probe: string;
  started_at: string;
}

const ORDER: SearchKind[] = ["application", "entity", "probe", "location", "connector", "runbook", "table", "column", "run"];
const MIN_PREFIX = 4;
const MASKED = "[masked]";

function rank(q: string, item: Searchable): number | undefined {
  const name = item.id.toLowerCase();
  const title = item.title.toLowerCase();
  if (name.startsWith(q) || name.split(".").at(-1)?.startsWith(q)) return 0;
  if (title.startsWith(q)) return 1;
  if (name.includes(q)) return 2;
  if (title.includes(q)) return 3;
  if ((item.also ?? []).some((a) => a.toLowerCase().includes(q))) return 4;
  return undefined;
}

function runMatches(q: string, run: SearchableRun): boolean {
  if (q.length >= MIN_PREFIX && run.id.toLowerCase().startsWith(q)) return true;
  return [run.subject, run.identifier_value].some((text) => {
    if (!text || text === MASKED) return false;
    const folded = text.toLowerCase();
    return folded === q || (q.length >= MIN_PREFIX && folded.startsWith(q));
  });
}

function shortTime(iso: string): string {
  const at = new Date(iso);
  const month = at.toLocaleString("en-GB", { month: "short", timeZone: "UTC" });
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(at.getUTCDate())} ${month} ${pad(at.getUTCHours())}:${pad(at.getUTCMinutes())}`;
}

/** The mock's answer: the groups that match, each cut at limit. */
export function mockSearch(q: string, limit: number, items: Searchable[], runs: SearchableRun[]): SearchResults {
  const folded = q.trim().toLowerCase();
  const groups: SearchGroup[] = [];
  for (const kind of ORDER) {
    let found: SearchItem[];
    if (kind === "run") {
      found = runs
        .filter((run) => runMatches(folded, run))
        .sort((a, b) => b.started_at.localeCompare(a.started_at))
        .map((run) => ({
          kind,
          application: run.application,
          id: run.id,
          title: run.subject ?? run.identifier_value,
          detail: `${run.status.charAt(0).toUpperCase()}${run.status.slice(1)} · ${shortTime(run.started_at)} · ${run.probe}`,
          labels: [],
        }));
    } else {
      found = items
        .filter((item) => item.kind === kind)
        .map((item) => ({ item, at: rank(folded, item) }))
        .filter((m): m is { item: Searchable; at: number } => m.at !== undefined)
        .sort((a, b) => a.at - b.at || a.item.id.localeCompare(b.item.id))
        .map(({ item }) => ({
          kind,
          application: item.application,
          id: item.id,
          title: item.title,
          detail: item.detail ?? null,
          labels: item.labels ?? [],
        }));
    }
    if (found.length > 0) groups.push({ kind, items: found.slice(0, limit), more: Math.max(0, found.length - limit) });
  }
  return { query: q.trim(), groups };
}
