// Author: @SaadZarook
import { expect, test } from "@playwright/test";
import type { LocationResult } from "../src/api/client";
import { locationName } from "../src/runFormat";

// Pure naming, no page: fan-out rows that share a title are told apart by their key (#82).
const controller = (row: number, parameters: Record<string, string>): LocationResult => ({
  location: "controller",
  title: "Door controller",
  parent: "cards",
  row,
  outcome: "stale",
  evidence: { connector: "door_controller_api", query: "GET /cards/{{card_id}}", parameters, guards: [], rows: [], rows_total: 0, started_at: "2026-01-01T10:00:00Z", duration_ms: 1 },
});

test("a fan-out row is named by the key it was looked up by", () => {
  expect(locationName(controller(1, { card_id: "C198220" }))).toBe("Door controller (card_id C198220)");
});

test("without a bound key, by its parent's row", () => {
  expect(locationName(controller(2, {}))).toBe("Door controller (cards row 3)");
});

test("a location that isn't a fan-out row keeps its title", () => {
  expect(locationName({ location: "ledger", title: "Ledger entries", outcome: "missing" })).toBe("Ledger entries");
});
