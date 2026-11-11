// Author: @SaadZarook
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test, type Page } from "@playwright/test";
import { parse } from "yaml";
import { expectNoOverflow } from "../e2e/overflow";

// A newcomer's first entity on a real MariaDB, as the v0.1 retest made it. dev/native/newcomer_live.sh
// serves the folder this needs, names it in PROBEOWL_LIVE_NEWCOMER_FOLDER, and picks the case:
// - unmarked: cards confirmed as Transaction, its link not in the catalog: the wizard fixes it inline;
// - suggested: cards not confirmed yet: the Tables screen confirms it with the link its import suggests.
const folder = process.env["PROBEOWL_LIVE_NEWCOMER_FOLDER"];
const scenario = process.env["PROBEOWL_LIVE_NEWCOMER_CASE"] ?? "unmarked";

test.skip(folder === undefined, "needs the newcomer's folder: run dev/native/newcomer_live.sh");
// A 1440 screen at 125%: the real names (access_uat_test_mariadb, its lookups) must fit (#256).
test.use({ viewport: { width: 1152, height: 900 } });

async function chooseApplication(page: Page, path: string) {
  await page.goto(path);
  await page.getByRole("button", { name: "Application" }).click();
  await page.getByRole("button", { name: /Access uat test/ }).last().click();
  await page.getByLabel("Environment").selectOption("uat");
}

/** The wizard from the root table to Add cards, on the Locations step. */
async function addCards(page: Page) {
  await chooseApplication(page, "/probes/new-entity");
  await page.getByLabel("Entity id").fill("visitor_profile");
  await page.getByRole("button", { name: "Next: Root table" }).click();
  await page.getByRole("combobox", { name: "Root table" }).selectOption({ label: "access_uat_test_mariadb · visitor_profile" });
  await expect(page.getByLabel(/Root key/)).toHaveValue("visitor_profile_id");
  await page.getByRole("button", { name: "Next: Locations" }).click();
  const follow = page.getByRole("region", { name: "Follow a key" });
  await expect(follow).toContainText("visitor_profile.id → access_uat_test_mariadb.cards.visitor_profile_id");
  await follow.getByRole("button", { name: "Add cards" }).click();
}

/** From a passing Locations step: card_id ticked, Create, and the file and catalog as they should be. */
async function createAndRead(page: Page) {
  await expectNoOverflow(page, "the wizard's Locations");
  await page.getByRole("button", { name: "Next: Identifiers" }).click();
  await expect(page.getByLabel("Find by cards.card_id")).toBeChecked();
  await expectNoOverflow(page, "the wizard's Identifiers");
  await expect(page.getByLabel("Find by visitor_profile.email")).not.toBeChecked();
  await page.getByRole("button", { name: "Next: Preview" }).click();
  await page.getByRole("button", { name: "Check" }).click();
  await page.getByRole("button", { name: "Create visitor_profile" }).click();
  await expect(page.getByRole("status", { name: "Entity created" })).toContainText("visitor_profile was created", { timeout: 30_000 });

  const written = parse(readFileSync(join(folder ?? "", "access_uat_test", "entities", "visitor_profile.yaml"), "utf-8")) as {
    locations: Record<string, { lookup: string }>;
    identifiers: Record<string, { via?: string; unique: boolean }>;
  };
  expect(Object.keys(written.locations)).toEqual(["visitor_profile", "cards"]);
  expect(written.locations["cards"]?.lookup).toContain("WHERE visitor_profile_id = :visitor_profile_id");
  expect(written.identifiers["card_id"]).toEqual({ via: "cards.card_id", unique: true });
  const catalog = readFileSync(join(folder ?? "", "access_uat_test", "catalog.yaml"), "utf-8");
  expect(catalog).toContain("links_to");
}

test("the wizard keeps cards, fixes its catalog link inline, and writes both locations with card_id", async ({ page }) => {
  test.skip(scenario !== "unmarked", "the unmarked case: newcomer_live.sh without --suggested");
  test.setTimeout(120_000);
  await addCards(page);
  // The real save checks refuse the lookup, on cards itself, with the fix the wizard knows.
  const failed = page.getByRole("region", { name: "Save checks failed for cards" });
  await expect(failed).toContainText("Time filter: cards is a Transaction table", { timeout: 30_000 });
  await failed.getByRole("button", { name: "Mark cards.visitor_profile_id as a link to visitor_profile.id" }).click();
  await expect(failed.getByLabel("Diff of access_uat_test/catalog.yaml")).toContainText("visitor_profile_id");
  await failed.getByRole("button", { name: "Confirm in catalog.yaml" }).click();
  await expect(page.getByText("Every location passes its save checks.")).toBeVisible({ timeout: 30_000 });
  await createAndRead(page);
});

test("the import suggests cards.visitor_profile_id as a link, so the wizard's cards passes at once", async ({ page }) => {
  test.skip(scenario !== "suggested", "the suggested case: newcomer_live.sh --suggested");
  test.setTimeout(120_000);
  await chooseApplication(page, "/tables");
  await page.getByRole("button", { name: /^cards/ }).click();
  const detail = page.getByRole("region", { name: "Table cards" });
  await expect(detail.getByLabel("Label")).toHaveValue("transaction");
  const target = detail.getByRole("group", { name: "Link target of visitor_profile_id" });
  await expect(target.getByLabel("Table")).toHaveValue("access_uat_test_mariadb.visitor_profile");
  await expect(target.getByLabel("Column")).toHaveValue("id");
  await detail.getByRole("button", { name: "Preview" }).click();
  await expect(detail.getByLabel("Diff of access_uat_test/catalog.yaml")).toContainText("links_to");
  await detail.getByRole("button", { name: "Confirm" }).click();
  await expect(detail.getByRole("status").filter({ hasText: "Written to" })).toBeVisible();

  await addCards(page);
  await expect(page.getByRole("region", { name: "Follow a key" })).not.toContainText("Add cards");
  await expect(page.getByText("Every location passes its save checks.")).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("region", { name: "Save checks failed for cards" })).toHaveCount(0);
  await createAndRead(page);
});
