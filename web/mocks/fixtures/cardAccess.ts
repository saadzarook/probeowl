// Author: @SaadZarook
import type { Entity, ExpectationEvaluation, LocationResult, ProbeSummary, Run } from "../../src/api/client";

/** The card-access visitor entity, shaped like examples/card-access/entities/visitor.yaml. */
export const visitorEntity: Entity = {
  entity: "visitor",
  title: "Visitor",
  root_key: "visitor_profile_id",
  owner: "access-team",
  identifiers: {
    visitor_profile_id: { pattern: "^VP\\d{8}$", unique: true, pii: false },
    card_id: { pattern: "^C\\d{6}$", via: "cards.card_id", unique: true, pii: false },
    employee_no: { via: "profile.employee_no", unique: false, pii: false },
    email: { pattern: "^[^@\\s]+@[^@\\s]+$", via: "profile.email", unique: false, pii: true },
  },
  locations: {
    profile: { title: "Visitor profile", using: "access_db", lookup: "SELECT id AS visitor_profile_id FROM visitor_profile WHERE id = :visitor_profile_id", cardinality: "one" },
    cards: {
      title: "Cards",
      using: "access_db",
      lookup: "SELECT card_id FROM cards WHERE visitor_profile_id = :visitor_profile_id",
      cardinality: "many",
      max_rows: 50,
      each: { controller: { title: "Door controller", using: "door_controller_api", lookup: "GET /cards/{{card_id}}", cardinality: "one", max_fanout: 10 } },
    },
  },
};

export const cardAccessProbes: ProbeSummary[] = [
  {
    name: "visitor_cards",
    title: "Visitor cards",
    kind: "inspect",
    entity: "visitor",
    owner: "access-team",
    description: "At most one active card, no active card past its end date, and the controller agrees.",
  },
];

const base: Omit<Run, "id" | "status" | "finished_at"> = {
  application: "card_access",
  environment: "uat",
  probe: "visitor_cards",
  probe_title: "Visitor cards",
  kind: "inspect",
  probe_version: "1",
  entity: "visitor",
  identifier: "visitor_profile_id",
  identifier_value: "VP00012931",
  actor: { kind: "person", name: "s.khan" },
  started_at: "2026-01-01T10:00:00Z",
};

function evidence(connector: string, query: string, rows: Record<string, unknown>[], rowsTotal?: number): LocationResult["evidence"] {
  return {
    connector,
    query,
    parameters: {},
    guards: ["Timeout 2 s", "Rows ≤ 50"],
    rows: rows as Record<string, never>[],
    rows_total: rowsTotal ?? rows.length,
    started_at: "2026-01-01T10:00:01Z",
    duration_ms: 42,
  };
}

const profileLocation = (profile: Record<string, unknown>): LocationResult => ({
  location: "profile", title: "Visitor profile",
  outcome: "found",
  evidence: evidence("access_db", "SELECT id AS visitor_profile_id, employee_no, type, status FROM visitor_profile WHERE id = :visitor_profile_id", [profile]),
});

const contractor = { visitor_profile_id: "VP00012931", name: "A••• R•••", type: "contractor", employee_no: "E-4471", status: "active" };

const plantedCards = [
  { card_id: "C204518", status: "active", valid_from: "2026-09-01", valid_to: "2026-12-31", access_group: "HQ-L3" },
  { card_id: "C198220", status: "active", valid_from: "2026-01-01", valid_to: "2026-09-30", access_group: "HQ-ALL" },
  { card_id: "C177031", status: "revoked", valid_from: "2025-01-01", valid_to: "2025-12-31", access_group: "HQ-L1" },
];

const controllerLookups: LocationResult[] = plantedCards.map((card, row) => ({
  location: "controller", title: "Door controller",
  parent: "cards",
  row,
  outcome: card.card_id === "C204518" ? "found" : "stale",
  reason: [undefined, "valid_to passed 30 days ago, still active", "revoked in database, still opens doors"][row],
  evidence: evidence("door_controller_api", `GET /cards/${card.card_id}`, [{ status: "active" }]),
}));

const expectations: ExpectationEvaluation[] = [
  { step: 0, expectation: "count(cards where status == 'active') <= 1", result: "fail", detail: "got 2" },
  { step: 1, expectation: "all(cards where status == 'active', valid_to > now())", result: "fail", detail: "C198220 expired" },
  { step: 2, expectation: "all(cards, controller.status == status)", result: "fail", detail: "C177031 differs" },
];

const contractorMatch = { subject: "VP00012931", fields: { type: "contractor", status: "active", created_at: "2024-03-02" } };

/** How an identifier found VP00012931: nothing for the root key, otherwise the via location, its resolution and the one match. */
function resolvedFrom(identifier: string): Pick<Run, "via" | "resolution" | "matches"> {
  const via = visitorEntity.identifiers?.[identifier]?.via;
  if (via === undefined || via === null) return { via: null, resolution: null, matches: [] };
  const [location = "", field = ""] = via.split(".");
  return {
    via,
    resolution: {
      location,
      // As owl serve does: the title of the location the identifier was found in.
      title: visitorEntity.locations[location]?.title ?? "",
      outcome: "found",
      evidence: evidence("access_db", `SELECT visitor_profile_id FROM ${location} WHERE ${field} = :${identifier}`, [{ visitor_profile_id: "VP00012931" }]),
    },
    matches: [contractorMatch],
  };
}

/** visitor_cards on VP00012931 (found from any of its card IDs), partway: the card lookup resolved it, the profile is in. */
export function runningCardRun(id: string, identifier: string, value: string): Run {
  return {
    ...base,
    id,
    identifier,
    identifier_value: value,
    subject: "VP00012931",
    ...resolvedFrom(identifier),
    status: "running",
    finished_at: null,
    locations: [profileLocation(contractor)],
  };
}

/** The three planted card issues on VP00012931: two active cards, one expired, one mismatch with the controller. */
export function plantedCardRun(id: string, identifier: string, value: string): Run {
  return {
    ...base,
    id,
    identifier,
    identifier_value: value,
    subject: "VP00012931",
    ...resolvedFrom(identifier),
    status: "failed",
    finished_at: "2026-01-01T10:00:02Z",
    locations: [
      profileLocation(contractor),
      {
        location: "cards", title: "Cards",
        outcome: "found",
        evidence: evidence("access_db", "SELECT card_id, status, valid_from, valid_to, access_group FROM cards WHERE visitor_profile_id = :visitor_profile_id", plantedCards),
      },
      ...controllerLookups,
    ],
    expectations,
  };
}

/** VP00008807: an inactive visitor with no cards. */
export function inactiveVisitorRun(id: string, identifier: string, value: string): Run {
  return {
    ...base,
    id,
    identifier,
    identifier_value: value,
    subject: "VP00008807",
    status: "failed",
    finished_at: "2026-01-01T10:00:02Z",
    locations: [
      profileLocation({ visitor_profile_id: "VP00008807", name: "A••• R•••", type: "visitor", employee_no: "E-1203", status: "inactive" }),
      { location: "cards", title: "Cards", outcome: "missing", reason: "0 rows", evidence: evidence("access_db", "SELECT card_id FROM cards WHERE visitor_profile_id = :visitor_profile_id", []) },
    ],
    expectations: [
      { step: 0, expectation: "count(cards where status == 'active') <= 1", result: "pass", detail: "got 0" },
      { step: 1, expectation: "all(cards where status == 'active', valid_to > now())", result: "skip", detail: "no cards" },
      { step: 2, expectation: "all(cards, controller.status == status)", result: "skip", detail: "no cards" },
    ],
  };
}

/** An email shared by the two planted profiles: Ambiguous, the matches and nothing else. The email is PII, so it arrives masked. */
export function ambiguousEmailRun(id: string, identifier: string): Run {
  const matches = [
    { subject: "VP00012931", fields: { type: "contractor", status: "active", created_at: "2024-03-02" } },
    { subject: "VP00008807", fields: { type: "visitor", status: "inactive", created_at: "2021-06-14" } },
  ];
  return {
    ...base,
    id,
    identifier,
    identifier_value: "[masked]",
    subject: null,
    via: "profile.email",
    resolution: {
      location: "profile", title: "Visitor profile",
      outcome: "ambiguous",
      reason: "2 subjects match",
      evidence: evidence("access_db", "SELECT id AS visitor_profile_id FROM visitor_profile WHERE email = :email", matches.map((m) => ({ visitor_profile_id: m.subject }))),
    },
    matches,
    status: "ambiguous",
    finished_at: "2026-01-01T10:00:01Z",
    locations: [],
  };
}

/** An identifier that finds nothing. */
export function unknownVisitorRun(id: string, identifier: string, value: string): Run {
  return {
    ...base,
    id,
    identifier,
    identifier_value: value,
    subject: null,
    status: "failed",
    finished_at: "2026-01-01T10:00:01Z",
    locations: [{ location: "profile", title: "Visitor profile", outcome: "missing", reason: "0 rows", evidence: evidence("access_db", "SELECT id AS visitor_profile_id FROM visitor_profile WHERE id = :visitor_profile_id", []) }],
  };
}

/** What every Production run of the dev stack warns about: the replica login cannot read the lag. */
export const replicaLagWarning = "Replica lag unknown for access_db (missing REPLICA MONITOR): Missing results can't be confirmed fresh";

/** A card-access run in Production: the same result, plus the replica lag warning. */
export function inProduction(run: Run): Run {
  return { ...run, environment: "prod", warnings: [replicaLagWarning] };
}

/** visitor_cards on VP00000042: the circuit breaker for access_db is open, so the run is skipped. */
export function skippedCardRun(id: string, identifier: string, value: string): Run {
  return {
    ...base,
    id,
    identifier,
    identifier_value: value,
    subject: "VP00000042",
    status: "skipped",
    skip_reason: "access_db is paused: replica lag 120 s (max 60 s); paused for another 42 s",
    finished_at: "2026-01-01T10:00:01Z",
    locations: [],
  };
}

/** visitor_cards on VP00000043: a kill switch stopped it before it read anything further. */
export function stoppedCardRun(id: string, identifier: string, value: string): Run {
  return {
    ...base,
    id,
    identifier,
    identifier_value: value,
    subject: "VP00000043",
    status: "failed",
    stop_reason: "stopped by the global kill switch: replica on fire",
    finished_at: "2026-01-01T10:00:01Z",
    locations: [],
    expectations: [],
  };
}
