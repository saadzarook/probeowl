// Author: @SaadZarook
import { stringify } from "yaml";
import type { LocationResult, Outcome, Run } from "./api/client";
import type { EntityDraft } from "./entityDraft";
import type { BadgeTone, GraphKey, GraphLocation } from "./ui";

// The entity graph's sample (#163): one real subject, looked up as the draft entity is built. Each
// lookup is a draft test run of a generated Inspect probe, through the same path and guards as the
// Probe editor's Test run; this file only turns the draft into that probe and the run into boxes.

/** How long the graph waits after the last edit before it looks the sample up again. */
export const SAMPLE_DEBOUNCE_MS = 1000;

/**
 * The Inspect probe a sample lookup runs: one expectation, since a probe needs one, that the root
 * location is there. The runner reads every location whatever the expectation says.
 */
export function sampleProbeYaml(draft: EntityDraft): string {
  const root = draft.locations[0]?.name ?? "root";
  return stringify(
    {
      probe: `${draft.entity}_sample`,
      title: `Sample of ${draft.title || draft.entity}`,
      kind: "inspect",
      entity: draft.entity,
      owner: draft.owner,
      expect: [`count(${root}) >= 1`],
    },
    { lineWidth: 0 },
  );
}

const TONES: Record<Outcome, BadgeTone> = {
  found: "found",
  missing: "missing",
  waiting: "waiting",
  blocked: "blocked",
  ambiguous: "ambiguous",
  error: "error",
  stale: "stale",
  not_configured: "not-configured",
};

/** The outcomes as every screen names them. */
export const OUTCOME_LABELS: Record<Outcome, string> = {
  found: "Found",
  missing: "Missing",
  waiting: "Waiting",
  blocked: "Blocked",
  ambiguous: "Ambiguous",
  error: "Error",
  stale: "Stale",
  not_configured: "Not configured",
};

// When a location has several results (one per parent row), the box shows the one that needs a look.
const WORST: Outcome[] = ["error", "missing", "blocked", "ambiguous", "stale", "waiting", "not_configured", "found"];

/** Where the sample's lookups are: none asked for yet, one under way, or a finished run. */
export type SampleState = { kind: "idle" } | { kind: "checking" } | { kind: "done"; run: Run };

/** One box's outcome on the sample, and the line under it. */
export interface BoxOutcome {
  tone: BadgeTone;
  label: string;
  detail?: string;
}

function rows(result: LocationResult): number | undefined {
  const evidence = result.evidence;
  if (!evidence) return undefined;
  return evidence.rows_total ?? evidence.rows?.length;
}

const plural = (count: number, word: string) => `${count} ${word}${count === 1 ? "" : "s"}`;

/** A location's outcome on the sample, from the run's results for it. */
export function boxOutcome(location: string, state: SampleState): BoxOutcome {
  if (state.kind === "checking") return { tone: "neutral", label: "Checking" };
  if (state.kind === "idle") return { tone: "neutral", label: "Not checked" };
  const results = (state.run.locations ?? []).filter((r) => r.location === location);
  if (results.length === 0) return { tone: "neutral", label: "Not checked", detail: "not looked up in the last check" };
  const worst = WORST.find((o) => results.some((r) => r.outcome === o)) ?? "found";
  const shown = results.find((r) => r.outcome === worst);
  const tone = TONES[worst];
  const label = OUTCOME_LABELS[worst];
  if (worst === "found") {
    const count = results.reduce((sum, r) => sum + (rows(r) ?? 0), 0);
    return { tone, label, detail: results.length > 1 ? `${results.length} lookups, ${plural(count, "row")}` : plural(count, "row") };
  }
  const reason = shown?.reason ?? undefined;
  if (results.length > 1) {
    const bad = results.filter((r) => r.outcome === worst).length;
    return { tone, label, detail: `${bad} of ${results.length}${reason ? `: ${reason}` : ""}` };
  }
  return reason ? { tone, label, detail: reason } : { tone, label };
}

/** The draft as the graph draws it: a box per location with its outcome, an arrow per key. */
export function graphOf(draft: EntityDraft, state: SampleState): { locations: GraphLocation[]; keys: GraphKey[] } {
  const locations = draft.locations.map((location, index): GraphLocation => {
    const outcome = boxOutcome(location.name, state);
    return {
      id: location.name,
      title: location.title || location.name,
      connector: location.connector,
      source: location.table,
      cardinality: location.cardinality,
      root: index === 0,
      outcome: { tone: outcome.tone, label: outcome.label },
      ...(outcome.detail ? { detail: outcome.detail } : {}),
    };
  });
  // A location with no key_from is looked up by the root key itself: its arrow comes from the root.
  const root = draft.locations[0];
  const keys = draft.locations.slice(1).flatMap((location): GraphKey[] => {
    if (location.keyFrom) return [{ from: location.keyFrom.location, to: location.name, field: location.keyFrom.field }];
    return root ? [{ from: root.name, to: location.name, field: draft.rootKey }] : [];
  });
  return { locations, keys };
}

/** "Last check" in one sentence, for sight and for screen readers: what each box came to. */
export function lastCheck(draft: EntityDraft, state: SampleState): string {
  if (state.kind === "checking") return "Checking the sample…";
  if (state.kind === "idle") return "Not checked yet.";
  const counts = new Map<string, number>();
  for (const location of draft.locations) {
    const { label } = boxOutcome(location.name, state);
    counts.set(label, (counts.get(label) ?? 0) + 1);
  }
  const parts = [...counts].map(([label, count]) => `${count} ${label}`);
  return `${plural(draft.locations.length, "location")}: ${parts.join(", ")}.`;
}
