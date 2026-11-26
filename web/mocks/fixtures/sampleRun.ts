// Author: @SaadZarook
import { parse } from "yaml";
import type { LocationResult, Run } from "../../src/api/client";
import { draftRun } from "./probeChecks";

// The entity graph's sample lookups (#163), answered from the draft entity that was sent, as the
// runner would read each location: a sample ending in zeros isn't there (the root is Missing and
// every other location Blocked); a webhook table has no row, the fault the graph exists to show
// before any probe is written; every other location is Found, one row or two.

interface DraftLocation {
  title?: string;
  using?: string;
  lookup?: string;
  key_from?: string;
  cardinality?: string;
  exposes?: string[];
  each?: Record<string, DraftLocation & { max_fanout?: number }>;
}

const NOT_THERE = /0{5,}$/;

// What a REST API answers, whatever the path: as the runner returns it, a field the location doesn't
// expose is masked whole, under its name (#165).
const REST_ANSWER: Record<string, unknown> = { id: "pay_7hq2m9", status: "captured", amount: "500.00", currency: "USD", customer: { tier: "gold" } };

function restRow(location: DraftLocation): Record<string, unknown> {
  const shown = new Set(location.exposes ?? []);
  return Object.fromEntries(Object.entries(REST_ANSWER).map(([field, value]) => [field, shown.has(field) ? value : "[masked]"]));
}

function evidence(location: DraftLocation, count: number): NonNullable<LocationResult["evidence"]> {
  const rest = (location.lookup ?? "").startsWith("GET ");
  return {
    connector: location.using ?? "",
    query: location.lookup ?? "",
    rows: Array.from({ length: count }, (_, i) => (rest ? restRow(location) : { row: i + 1 })),
    rows_total: count,
    started_at: "2026-11-22T10:00:00Z",
    duration_ms: 12,
  };
}

/** A sample lookup of a draft entity: running at first, then each location's outcome. */
export function sampleRun(id: string, entityYaml: string, value: string, running: boolean): Run {
  const entity = parse(entityYaml) as { entity?: string; locations?: Record<string, DraftLocation> };
  const locations = Object.entries(entity.locations ?? {});
  const root = locations[0]?.[0];
  const missingRoot = NOT_THERE.test(value);
  const results: LocationResult[] = locations.flatMap(([name, location]): LocationResult[] => {
    const own = ownResult(name, location);
    if (own.outcome !== "found") return [own];
    // A fan-out, once for each of the two rows, up to max_fanout: the second has no row.
    const each = Object.entries(location.each ?? {}).flatMap(([child, spec]) =>
      Array.from({ length: Math.min(2, spec.max_fanout ?? 2) }, (_, row): LocationResult =>
        row === 0
          ? { location: child, title: spec.title ?? child, parent: name, row, outcome: "found", evidence: evidence(spec, 1) }
          : { location: child, title: spec.title ?? child, parent: name, row, outcome: "missing", reason: "no row", evidence: evidence(spec, 0) },
      ),
    );
    return [own, ...each];
  });
  function ownResult(name: string, location: DraftLocation): LocationResult {
    const title = location.title ?? name;
    if (missingRoot && name === root) return { location: name, title, outcome: "missing", reason: `no row for ${value}`, evidence: evidence(location, 0) };
    if (missingRoot) return { location: name, title, outcome: "blocked", reason: `${root} is Missing` };
    if (/webhook/.test(location.lookup ?? "")) return { location: name, title, outcome: "missing", reason: "no row", evidence: evidence(location, 0) };
    return { location: name, title, outcome: "found", evidence: evidence(location, location.cardinality === "many" ? 2 : 1) };
  }
  const base = draftRun(id);
  return {
    ...base,
    probe: `${entity.entity ?? "entity"}_sample`,
    kind: "inspect",
    entity: entity.entity ?? "entity",
    subject: value,
    identifier_value: value,
    status: running ? "running" : missingRoot ? "failed" : "passed",
    locations: running ? [] : results,
    expectations: running ? [] : [{ step: 0, expectation: `count(${root}) >= 1`, result: missingRoot ? "fail" : "pass", detail: missingRoot ? "0" : "1", not_applicable: false }],
    not_applicable: [],
    caveat: null,
  };
}
