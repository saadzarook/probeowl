// Author: @SaadZarook
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

// The entity wizard against the mocks: from the imported tables to a checked, written entity.

async function start(page: Page, application: RegExp, environment: string) {
  await page.goto("/probes/new-entity");
  await page.getByRole("button", { name: "Application" }).click();
  await page.getByRole("button", { name: application }).last().click();
  await page.getByLabel("Environment").selectOption(environment);
}

async function next(page: Page, step: string) {
  await page.getByRole("button", { name: `Next: ${step}` }).click();
}

async function nameAndRoot(page: Page, entity: string, root: string) {
  await page.getByLabel("Entity id").fill(entity);
  await next(page, "Root table");
  await page.getByRole("combobox", { name: "Root table" }).selectOption({ label: root });
  await next(page, "Locations");
}

test("a visitor's cards: the catalog's link is offered, many with 100 rows, and the entity is written", async ({ page }) => {
  await start(page, /Card access/, "prod");
  await page.getByLabel("Entity id").fill("badge_holder");
  await next(page, "Root table");
  await page.getByRole("combobox", { name: "Root table" }).selectOption({ label: "access_db · visitor_profile" });
  // visitor_profile's key is just id: the root key gets the entity's name.
  await expect(page.getByLabel(/Root key/)).toHaveValue("badge_holder_id");
  await next(page, "Locations");
  const follow = page.getByRole("region", { name: "Follow a key" });
  await expect(follow).toContainText("visitor_profile.id → access_db.cards.visitor_profile_id (the catalog links these columns)");
  await expect(follow).not.toContainText(".status");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await follow.getByRole("button", { name: "Add cards" }).click();
  const cards = page.getByRole("listitem", { name: "Location cards" });
  await expect(cards.getByLabel("Records per subject")).toHaveValue("many");
  await expect(cards.getByLabel(/Most rows/)).toHaveValue("100");

  await expect(page.getByText("Every location passes its save checks.")).toBeVisible();

  await next(page, "Identifiers");
  // card_id is cards' primary key and not personal data: ticked for the person, with the reason.
  await expect(page.getByLabel("Find by cards.card_id")).toBeChecked();
  const identifierList = page.getByRole("region", { name: "Identifiers" });
  await expect(identifierList.getByRole("listitem").filter({ hasText: "cards.card_id" })).toContainText("unique: cards' primary key");
  // email is neither unique nor named like an identifier: it is under More columns.
  await page.getByText(/^More columns/).click();
  await expect(page.getByLabel("Find by visitor_profile.email")).not.toBeChecked();
  await page.getByLabel("Find by visitor_profile.email").check();
  const identifiers = page.getByRole("region", { name: "Identifiers" });
  await expect(identifiers.getByRole("listitem").filter({ hasText: "cards.card_id" }).getByLabel("Unique")).toBeChecked();
  await expect(identifiers.getByRole("listitem").filter({ hasText: "visitor_profile.email" }).getByLabel("PII")).toBeChecked();

  await next(page, "Preview");
  const yaml = page.getByLabel("badge_holder.yaml");
  await expect(yaml).toContainText("root_key: badge_holder_id");
  await expect(yaml).toContainText("max_rows: 100");
  await expect(yaml).toContainText("via: cards.card_id");
  await expect(yaml).not.toContainText("key_from"); // cards is keyed by the root key itself
  // ... and exposes its key column as the root key, so card_id can find a subject.
  await expect(yaml).toContainText("SELECT card_id, visitor_profile_id AS badge_holder_id, status");
  await page.getByRole("button", { name: "Check" }).click();
  await expect(page.getByLabel("Diff of card-access/entities/badge_holder.yaml")).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.getByRole("button", { name: "Create badge_holder" }).click();
  const created = page.getByRole("status", { name: "Entity created" });
  await expect(created).toContainText("badge_holder was created");
  await expect(created.getByRole("link", { name: "Write a probe for badge_holder" })).toHaveAttribute("href", "/probes?new=inspect&entity=badge_holder");
});

test("Identifiers lists unique and id-like columns first, the rest under More columns", async ({ page }) => {
  await start(page, /Card access/, "prod");
  await nameAndRoot(page, "badge_holder_ids", "access_db · visitor_profile");
  await page.getByRole("region", { name: "Follow a key" }).getByRole("button", { name: "Add cards" }).click();
  await next(page, "Identifiers");
  const identifiers = page.getByRole("region", { name: "Identifiers" });
  // card_id first: unique. status, type and the like are not what a person pastes.
  const shown = identifiers.locator(":scope > ul > li");
  await expect(shown.first()).toContainText("cards.card_id");
  await expect(shown.filter({ hasText: ".status" })).toHaveCount(0);
  const more = identifiers.locator("details.more-columns");
  await expect(more.locator("summary")).toContainText("More columns");
  await expect(more).not.toHaveAttribute("open", "");
  await more.locator("summary").click();
  await expect(more.getByLabel("Find by cards.status")).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});

test("a top-up's ledger entries come from another connector, keyed from the top-up", async ({ page }) => {
  await start(page, /Wallet/, "prod");
  await nameAndRoot(page, "topup_ledger", "wallet_db · topups");
  const follow = page.getByRole("region", { name: "Follow a key" });
  await expect(follow).toContainText("topups.ledger_txn_id → ledger_db.ledger_entries.txn_id");
  await expect(follow).toContainText("topups.user_id → wallet_db.wallets.user_id (same column name)");
  await follow.getByRole("button", { name: "Add ledger_entries" }).click();
  await expect(page.getByRole("listitem", { name: "Location ledger_entries" })).toContainText("ledger_db · ledger_entries · by txn_id from topups.ledger_txn_id");
  await next(page, "Identifiers");
  await next(page, "Preview");
  const yaml = page.getByLabel("topup_ledger.yaml");
  await expect(yaml).toContainText("using: ledger_db");
  await expect(yaml).toContainText("key_from: topups.ledger_txn_id");
  await expect(yaml).toContainText("WHERE txn_id = :ledger_txn_id");
});

test("Link by columns makes a lookup the save checks still judge: here the index check fails it", async ({ page }) => {
  await start(page, /Card access/, "prod");
  await nameAndRoot(page, "card_status", "access_db · visitor_profile");
  await page.getByRole("button", { name: "Link by columns…" }).click();
  await page.getByLabel("Column of a location").selectOption("visitor_profile.status");
  await page.getByLabel("New table").selectOption({ label: "access_db · cards" });
  await page.getByLabel("Its column").selectOption("status");
  await page.getByRole("button", { name: "Add the location" }).click();
  await expect(page.getByRole("listitem", { name: "Location cards" })).toContainText("by status from visitor_profile.status");
  // The failure shows on the location it is about, as soon as the location is added; the location stays.
  await expect(page.getByRole("region", { name: "Save checks failed for cards" })).toContainText("Index check: the lookup would read all of cards: no index on status");
  await next(page, "Identifiers");
  await next(page, "Preview");
  await expect(page.getByRole("alert").filter({ hasText: "cards fails a save check" })).toBeVisible();
  await expect(page.getByLabel("card_status.yaml")).toContainText("cards:");
  await page.getByRole("button", { name: "Check" }).click();
  await expect(page.getByLabel("Failed save checks")).toContainText("Index check: cards: the lookup would read all of cards: no index on status");
  await expect(page.getByRole("button", { name: "Create card_status" })).toBeDisabled();
  await page.getByRole("button", { name: "Back to Locations" }).click();
  await expect(page.getByRole("listitem", { name: "Location cards" })).toBeVisible();
});

test("a Transaction table read by a column the catalog does not call a key: the link is marked inline, then the entity is created", async ({ page }) => {
  await start(page, /Wallet/, "prod");
  await nameAndRoot(page, "wallet_topups", "wallet_db · wallets");
  await page.getByRole("region", { name: "Follow a key" }).getByRole("button", { name: "Add topups" }).click();
  const failed = page.getByRole("region", { name: "Save checks failed for topups" });
  await expect(failed).toContainText("Time filter: topups is a Transaction table: filter on its time column created_at");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  // The fix is the link the wizard followed, written to catalog.yaml through its preview.
  await failed.getByRole("button", { name: "Mark topups.user_id as a link to wallets.user_id" }).click();
  await expect(failed.getByLabel("Diff of wallet/catalog.yaml")).toContainText("links_to: {connector: wallet_db, table: wallets, column: user_id}");
  await failed.getByRole("button", { name: "Confirm in catalog.yaml" }).click();
  // The catalog changed, so the checks run again, and topups passes.
  await expect(page.getByText("Every location passes its save checks.")).toBeVisible();
  await expect(page.getByRole("listitem", { name: "Location topups" })).toBeVisible();
  await next(page, "Identifiers");
  await next(page, "Preview");
  await page.getByRole("button", { name: "Check" }).click();
  await page.getByRole("button", { name: "Create wallet_topups" }).click();
  await expect(page.getByRole("status", { name: "Entity created" })).toContainText("wallet_topups was created");
});

test("an entity id that exists is caught on step 1, as it is typed", async ({ page }) => {
  await start(page, /Card access/, "prod");
  await page.getByLabel("Entity id").fill("visitor");
  const taken = page.getByRole("alert");
  await expect(taken).toHaveText("visitor already exists: open it, or choose another name.");
  await expect(taken.getByRole("link", { name: "open it" })).toHaveAttribute("href", "/probes?new=inspect&entity=visitor");
  await expect(page.getByLabel("Entity id")).toHaveAttribute("aria-invalid", "true");
  await expect(page.getByRole("button", { name: "Next: Root table" })).toBeDisabled();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  // Another name clears it.
  await page.getByLabel("Entity id").fill("visitor_badges");
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Next: Root table" })).toBeEnabled();
});

test("a connector not imported here says how to import it", async ({ page }) => {
  await start(page, /Wallet/, "uat");
  await page.getByLabel("Entity id").fill("topup_check");
  await next(page, "Root table");
  await expect(page.getByText("wallet_db has not been imported in uat; run: owl catalog import wallet_db --application wallet --env uat")).toBeVisible();
  // ledger_db is imported in uat, so its tables are still offered.
  await expect(page.getByRole("combobox", { name: "Root table" }).locator("option", { hasText: "ledger_db · ledger_entries" })).toHaveCount(1);
});

test("the Probe editor offers a new entity", async ({ page }) => {
  await page.goto("/probes");
  await expect(page.getByRole("link", { name: "New entity…" })).toHaveAttribute("href", "/probes/new-entity");
});
