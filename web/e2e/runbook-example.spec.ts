// Author: @SaadZarook
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";
import { runbookExample } from "../src/runbooks";

// The empty state's minimal runbook is exactly the file the backend test loads and checks
// (backend/tests/test_runbook_example.py), so what a newcomer copies is a runbook owl serve loads.
const CHECKED = fileURLToPath(new URL("../../backend/tests/data/runbook_example.yaml", import.meta.url));

test("the minimal runbook for Card access is the one the backend checks", () => {
  const example = runbookExample({ application: "card_access", owner: "access-team", today: "2026-11-09", entity: "visitor", location: "profile", probe: "visitor_cards" });
  expect(example).toBe(readFileSync(CHECKED, "utf8").replace(/\r\n/g, "\n"));
});

test("without an entity or probe it says what to fill in, and has no probe step", () => {
  const example = runbookExample({ application: "orders", owner: "orders-team", today: "2026-11-09" });
  expect(example).toContain("applies_to: { entity: <entity>, when: [<location>.missing] }");
  expect(example).not.toContain("kind: probe");
});
