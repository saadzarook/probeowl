// Author: @SaadZarook
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type Request } from "@playwright/test";

// New entity as the entity graph (#163), against the mocks: a root table, a sample, a box per
// location with its outcome on that sample, looked up again a second after each change outside
// Production, and only on Check sample in Production.

async function start(page: Page, application: RegExp, environment?: string) {
  await page.goto("/probes/new-entity");
  await page.getByRole("button", { name: "Application" }).click();
  await page.getByRole("button", { name: application }).last().click();
  if (environment) await page.getByRole("banner").getByLabel("Environment").selectOption(environment);
}

/** The sample lookups the page starts: each a draft test run. */
function lookups(page: Page): Request[] {
  const seen: Request[] = [];
  page.on("request", (request) => {
    if (request.method() === "POST" && request.url().endsWith("/probes/test-runs")) seen.push(request);
  });
  return seen;
}

/** Wallet's webhooks, whose key the catalog doesn't link: the mocks answer them Missing. */
async function linkWebhooks(page: Page) {
  await page.getByRole("button", { name: "Link by columns…" }).click();
  await page.getByLabel("Column of a location").selectOption("topups.provider_payment_id");
  await page.getByLabel("New table").selectOption({ label: "wallet_db · provider_webhooks" });
  await page.getByLabel("Its column").selectOption("payment_id");
  await page.getByRole("button", { name: "Add the location" }).click();
}

const graph = (page: Page) => page.getByRole("group", { name: /^Entity graph of / });
const lastCheck = (page: Page) => page.getByRole("status", { name: "Last check" });

test("a visitor built against a sample: each box shows its outcome, and the entity is written", async ({ page }) => {
  const sent = lookups(page);
  await start(page, /Card access/, "uat");
  await page.getByRole("combobox", { name: "Root table" }).selectOption({ label: "access_db · visitor_profile" });
  // The id comes from the table; the root key from the id.
  await expect(page.getByLabel("Entity id")).toHaveValue("visitor_profile");
  await expect(page.getByLabel(/Root key/)).toHaveValue("visitor_profile_id");
  await expect(lastCheck(page)).toHaveText("Last check: no sample yet.");
  expect(sent).toHaveLength(0);

  await page.getByLabel("Sample", { exact: true }).fill("VP00012931");
  await expect(graph(page).getByRole("button", { name: "Visitor profile Found" })).toBeVisible();
  await expect(lastCheck(page)).toHaveText("Last check: 1 location: 1 Found.");
  expect(sent).toHaveLength(1);
  const body = sent[0]?.postDataJSON() as { probe: string; entity: string; value: string; environment: string; investigation: { reason: string; notes: string } };
  expect(body.probe).toContain("kind: inspect");
  expect(body.probe).toContain("count(visitor_profile) >= 1");
  expect(body.entity).toContain("entity: visitor_profile");
  expect([body.value, body.environment, body.investigation.reason]).toEqual(["VP00012931", "uat", "proactive_check"]);
  expect(body.investigation.notes).toBe("Entity graph: looking up the sample");

  // Follow a key: Cards is added, keyed by the root key, and looked up again on its own.
  const follow = page.getByRole("region", { name: "Follow a key" });
  await expect(follow).toContainText("visitor_profile.id → access_db.cards.visitor_profile_id (the catalog links these columns)");
  await expect(follow).not.toContainText(".status");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await follow.getByRole("button", { name: "Add cards" }).click();
  const cardsLocation = page.getByRole("listitem", { name: "Location cards" });
  await expect(cardsLocation.getByLabel("Records per subject")).toHaveValue("many");
  await expect(cardsLocation.getByLabel(/Most rows/)).toHaveValue("100");
  await expect(page.getByText("Every location passes its save checks.")).toBeVisible();
  const cards = graph(page).getByRole("button", { name: "Cards Found" });
  await expect(cards).toBeVisible();
  await expect(cards).toHaveAccessibleDescription("access_db, cards; many; key visitor_profile_id from Visitor profile; 2 rows");
  await expect(lastCheck(page)).toHaveText("Last check: 2 locations: 2 Found.");
  await expect(page.getByRole("region", { name: "Cards: details" })).toContainText("Found: 2 rows");
  expect(sent).toHaveLength(2);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);

  // card_id is cards' primary key and not personal data: ticked for the person, with the reason.
  const identifiers = page.getByRole("region", { name: "Identifiers" });
  await expect(page.getByLabel("Find by cards.card_id")).toBeChecked();
  await expect(identifiers.getByRole("listitem").filter({ hasText: "cards.card_id" })).toContainText("unique: cards' primary key");
  await expect(identifiers.getByRole("listitem").filter({ hasText: "cards.card_id" }).getByLabel("Unique")).toBeChecked();
  // email is personal data: not ticked, under More columns, and marked PII when it is.
  await page.getByText(/^More columns/).click();
  await expect(page.getByLabel("Find by visitor_profile.email")).not.toBeChecked();
  await page.getByLabel("Find by visitor_profile.email").check();
  await expect(identifiers.getByRole("listitem").filter({ hasText: "visitor_profile.email" }).getByLabel("PII")).toBeChecked();

  // The whole file, before anything is asked of it.
  const yaml = page.getByLabel("visitor_profile.yaml", { exact: true });
  await expect(yaml).toContainText("root_key: visitor_profile_id");
  await expect(yaml).toContainText("max_rows: 100");
  await expect(yaml).toContainText("via: cards.card_id");
  await expect(yaml).not.toContainText("key_from"); // cards is keyed by the root key itself
  await expect(yaml).toContainText("SELECT card_id, visitor_profile_id, status");

  // Create previews the file with its save checks; Write writes it.
  await page.getByRole("button", { name: "Create visitor_profile" }).click();
  await expect(page.getByLabel("Diff of card-access/entities/visitor_profile.yaml")).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.getByRole("button", { name: "Write card-access/entities/visitor_profile.yaml" }).click();
  const created = page.getByRole("status", { name: "Entity created" });
  await expect(created).toContainText("visitor_profile was created");
  await expect(created.getByRole("link", { name: "Write a probe for visitor_profile" })).toHaveAttribute("href", "/probes?new=validate&entity=visitor_profile");
});

test("typing the sample starts one lookup, a second after the last key, and each later change one more", async ({ page }) => {
  const sent = lookups(page);
  await start(page, /Card access/, "uat");
  await page.getByRole("combobox", { name: "Root table" }).selectOption({ label: "access_db · visitor_profile" });
  await page.getByLabel("Sample", { exact: true }).pressSequentially("VP00012931", { delay: 80 });
  await expect(lastCheck(page)).toHaveText("Last check: 1 location: 1 Found.");
  expect(sent).toHaveLength(1);
  expect((sent[0]?.postDataJSON() as { value: string }).value).toBe("VP00012931");
  await page.getByRole("region", { name: "Follow a key" }).getByRole("button", { name: "Add cards" }).click();
  await expect(lastCheck(page)).toHaveText("Last check: 2 locations: 2 Found.");
  expect(sent).toHaveLength(2);
});

test("a sample that isn't there: the root is Missing, every other box Blocked", async ({ page }) => {
  await start(page, /Card access/, "uat");
  await page.getByRole("combobox", { name: "Root table" }).selectOption({ label: "access_db · visitor_profile" });
  await page.getByRole("region", { name: "Follow a key" }).getByRole("button", { name: "Add cards" }).click();
  await page.getByLabel("Sample", { exact: true }).fill("VP00000000");
  await expect(lastCheck(page)).toHaveText("Last check: 2 locations: 1 Missing, 1 Blocked.");
  await expect(graph(page).getByRole("button", { name: "Visitor profile Missing" })).toHaveAccessibleDescription(/no row for VP00000000$/);
  await expect(graph(page).getByRole("button", { name: "Cards Blocked" })).toHaveAccessibleDescription(/visitor_profile is Missing$/);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});

test("the graph opens outside Production; in Production it looks up only on Check sample, asking once a session", async ({ page }) => {
  const sent = lookups(page);
  // Production chosen elsewhere: the graph opens in the first environment that isn't.
  await page.goto("/inspect");
  await page.getByRole("button", { name: "Application" }).click();
  await page.getByRole("button", { name: /Card access/ }).last().click();
  await page.getByRole("banner").getByLabel("Environment").selectOption("prod");
  await page.getByRole("link", { name: "Probes" }).click();
  await page.getByRole("link", { name: "New entity…" }).click();
  await expect(page.getByRole("banner").getByLabel("Environment")).toHaveValue("uat");
  const banner = page.locator(".prod-banner");
  await expect(banner).toHaveCount(0);

  // Production on purpose: the red banner shows, and nothing is ever looked up live.
  await page.getByRole("banner").getByLabel("Environment").selectOption("prod");
  await expect(banner).toContainText("Reason and Reference required");
  await page.getByRole("combobox", { name: "Root table" }).selectOption({ label: "access_db · visitor_profile" });
  await page.getByLabel("Sample", { exact: true }).fill("VP00012931");
  const check = page.getByRole("button", { name: "Check sample" });
  await expect(check).toBeDisabled();
  await page.waitForTimeout(1500); // longer than the live wait: still nothing sent
  expect(sent).toHaveLength(0);
  await expect(graph(page).getByRole("button", { name: "Visitor profile Not checked" })).toBeVisible();
  await page.getByLabel("Reason", { exact: true }).selectOption("incident");
  await page.getByLabel("Reference", { exact: true }).fill("ACCESS-311");
  await check.click();
  await expect(lastCheck(page)).toHaveText("Last check: 1 location: 1 Found.");
  expect(sent).toHaveLength(1);
  const body = sent[0]?.postDataJSON() as { environment: string; investigation: { reason: string; reference: string } };
  expect([body.environment, body.investigation.reason, body.investigation.reference]).toEqual(["prod", "incident", "ACCESS-311"]);
  // A change is not looked up until checked again.
  await page.getByRole("region", { name: "Follow a key" }).getByRole("button", { name: "Add cards" }).click();
  await expect(lastCheck(page)).toHaveText("Last check: Not checked yet.");
  await expect(graph(page).getByRole("button", { name: "Cards Not checked" })).toBeVisible();
  await page.waitForTimeout(1500);
  expect(sent).toHaveLength(1);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  // The reason and reference are kept for the session.
  await page.reload();
  await page.getByRole("banner").getByLabel("Environment").selectOption("prod");
  await expect(page.getByLabel("Reason", { exact: true })).toHaveValue("incident");
  await expect(page.getByLabel("Reference", { exact: true })).toHaveValue("ACCESS-311");
});

test("a lookup that can't start says why, with Retry", async ({ page }) => {
  // The mocks refuse this sample's first lookup with the run limit, as the server would (429).
  await start(page, /Card access/, "uat");
  await page.getByRole("combobox", { name: "Root table" }).selectOption({ label: "access_db · visitor_profile" });
  await page.getByLabel("Sample", { exact: true }).fill("VP00000429");
  const alert = page.getByRole("alert").filter({ hasText: "Too many runs at once" });
  await expect(alert).toBeVisible();
  await expect(lastCheck(page)).toHaveText("Last check: Not checked yet.");
  await alert.getByRole("button", { name: "Retry" }).click();
  await expect(lastCheck(page)).toHaveText("Last check: 1 location: 1 Found.");
  await expect(alert).toHaveCount(0);
});

const SHOTS = process.env["SHOTS"];
for (const theme of ["light", "dark"] as const) {
  for (const width of [1440, 390]) {
    test(`the graph at ${width}, ${theme}: ${width < 1024 ? "the Outline" : "boxes and arrows"}, axe clean, nothing scrolls sideways`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: theme });
      await page.setViewportSize({ width, height: 900 });
      await start(page, /Card access/, "uat");
      await page.getByRole("combobox", { name: "Root table" }).selectOption({ label: "access_db · visitor_profile" });
      await page.getByRole("region", { name: "Follow a key" }).getByRole("button", { name: "Add cards" }).click();
      await page.getByLabel("Sample", { exact: true }).fill("VP00012931");
      await expect(lastCheck(page)).toHaveText("Last check: 2 locations: 2 Found.");
      if (width < 1024) {
        await expect(graph(page)).toHaveCount(0);
        await expect(page.getByRole("list", { name: "Outline of visitor_profile" }).getByRole("button", { name: "Cards Found" })).toBeVisible();
      } else {
        await expect(graph(page).getByRole("button", { name: "Cards Found" })).toBeVisible();
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
      expect((await new AxeBuilder({ page }).include("main").analyze()).violations).toEqual([]);
      if (SHOTS) await page.screenshot({ path: `${SHOTS}/new-entity-${theme}-${width}.png`, fullPage: true });
    });
  }
}

// What the wizard did, on the one screen.

test("Identifiers lists unique and id-like columns first, the rest under More columns", async ({ page }) => {
  await start(page, /Card access/, "uat");
  await page.getByLabel("Entity id").fill("badge_holder_ids");
  await page.getByRole("combobox", { name: "Root table" }).selectOption({ label: "access_db · visitor_profile" });
  await expect(page.getByLabel(/Root key/)).toHaveValue("badge_holder_ids_id");
  await page.getByRole("region", { name: "Follow a key" }).getByRole("button", { name: "Add cards" }).click();
  const identifiers = page.getByRole("region", { name: "Identifiers" });
  const shown = identifiers.locator(":scope > ul > li");
  await expect(shown.first()).toContainText("cards.card_id");
  await expect(page.getByLabel("Find by cards.card_id")).toBeChecked();
  await expect(shown.filter({ hasText: ".status" })).toHaveCount(0);
  const more = identifiers.locator("details.more-columns");
  await expect(more).not.toHaveAttribute("open", "");
  await more.locator("summary").click();
  await expect(more.locator("summary")).toContainText("More columns");
  await expect(more.getByLabel("Find by cards.status")).toBeVisible();
  await expect(page.getByLabel("Find by visitor_profile.email")).not.toBeChecked();
  // A root key under another name: cards exposes its key column by it, so card_id finds a subject.
  const yaml = page.getByLabel("badge_holder_ids.yaml", { exact: true });
  await expect(yaml).toContainText("SELECT card_id, visitor_profile_id AS badge_holder_ids_id, status");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});

test("a top-up's ledger entries come from another connector, keyed from the top-up", async ({ page }) => {
  await start(page, /Wallet/, "prod");
  await page.getByRole("combobox", { name: "Root table" }).selectOption({ label: "wallet_db · topups" });
  const follow = page.getByRole("region", { name: "Follow a key" });
  await expect(follow).toContainText("topups.ledger_txn_id → ledger_db.ledger_entries.txn_id");
  await expect(follow).toContainText("topups.user_id → wallet_db.wallets.user_id (same column name)");
  await follow.getByRole("button", { name: "Add ledger_entries" }).click();
  await expect(page.getByRole("listitem", { name: "Location ledger_entries" })).toContainText("ledger_db · ledger_entries · by txn_id from topups.ledger_txn_id");
  await expect(graph(page).getByRole("button", { name: "Ledger entries Not checked" })).toHaveAccessibleDescription(/key ledger_txn_id from Topups/);
  const yaml = page.getByLabel("topups.yaml", { exact: true });
  await expect(yaml).toContainText("using: ledger_db");
  await expect(yaml).toContainText("key_from: topups.ledger_txn_id");
  await expect(yaml).toContainText("WHERE txn_id = :ledger_txn_id");
  await page.getByRole("button", { name: "Create topups" }).click();
  await expect(page.getByLabel("Diff of wallet/entities/topups.yaml")).toContainText("key_from: topups.ledger_txn_id");
});

test("Link by columns makes a lookup the save checks still judge: here the plan check refuses it", async ({ page }) => {
  await start(page, /Card access/, "uat");
  await page.getByLabel("Entity id").fill("card_status");
  await page.getByRole("combobox", { name: "Root table" }).selectOption({ label: "access_db · visitor_profile" });
  await page.getByRole("button", { name: "Link by columns…" }).click();
  await page.getByLabel("Column of a location").selectOption("visitor_profile.status");
  await page.getByLabel("New table").selectOption({ label: "access_db · cards" });
  await page.getByLabel("Its column").selectOption("status");
  await page.getByRole("button", { name: "Add the location" }).click();
  await expect(page.getByRole("listitem", { name: "Location cards" })).toContainText("by status from visitor_profile.status");
  await expect(page.getByRole("region", { name: "Save checks failed for cards" })).toContainText("access_db plan · MariaDB: Table Scan of cards: the lookup would read all of cards. Index the column it filters on");
  // The location stays in the file until a person removes it; the entity can't be created meanwhile.
  await expect(page.getByRole("alert").filter({ hasText: "cards fails a save check" })).toBeVisible();
  await expect(page.getByLabel("card_status.yaml", { exact: true })).toContainText("cards:");
  await expect(page.getByRole("button", { name: "Create card_status" })).toBeDisabled();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.getByRole("button", { name: "Remove cards" }).click();
  await expect(page.getByRole("button", { name: "Create card_status" })).toBeEnabled();
});

test("in Production nothing is read on an edit: the save checks and the lookup wait for Check sample, or Create", async ({ page }) => {
  const sent = lookups(page);
  const checked: string[] = [];
  page.on("request", (request) => {
    if (request.method() === "PUT" && request.url().includes("/entities/")) checked.push((request.postDataJSON() as { environment: string }).environment);
  });
  await start(page, /Card access/, "prod");
  await page.getByRole("combobox", { name: "Root table" }).selectOption({ label: "access_db · visitor_profile" });
  await page.getByRole("region", { name: "Follow a key" }).getByRole("button", { name: "Add cards" }).click();
  await page.getByLabel("Sample", { exact: true }).fill("VP00012931");
  await page.getByLabel("Reason", { exact: true }).selectOption("incident");
  await page.getByLabel("Reference", { exact: true }).fill("ACCESS-311");
  await page.getByRole("listitem", { name: "Location cards" }).getByLabel(/Most rows/).fill("50");
  await page.getByLabel("Title").fill("Visitor");
  await page.evaluate(() => window.dispatchEvent(new Event("visibilitychange")));
  await page.waitForTimeout(3000); // longer than the checks' and the lookup's waits together
  expect(checked).toEqual([]);
  expect(sent).toHaveLength(0);
  await expect(page.getByText(/run only when you press Check sample or Create/)).toBeVisible();
  await expect(page.getByText("Every location passes its save checks.")).toHaveCount(0);

  // Check sample: the checks once, then the lookup.
  await page.getByRole("button", { name: "Check sample" }).click();
  await expect(lastCheck(page)).toHaveText("Last check: 2 locations: 2 Found.");
  expect(checked).toEqual(["prod"]);
  expect(sent).toHaveLength(1);
  await expect(page.getByText("Every location passes its save checks.")).toBeVisible();

  // An edit makes both out of date, and starts neither; Create runs the checks, not a lookup.
  await page.getByRole("listitem", { name: "Location cards" }).getByLabel(/Most rows/).fill("60");
  await page.waitForTimeout(3000);
  expect(checked).toEqual(["prod"]);
  await page.getByRole("button", { name: "Create visitor_profile" }).click();
  await expect(page.getByLabel("Diff of card-access/entities/visitor_profile.yaml")).toBeVisible();
  expect(checked).toEqual(["prod", "prod"]);
  expect(sent).toHaveLength(1);
  await expect(page.getByText("Every location passes its save checks.")).toBeVisible();
});

test("in Production a failing check stops Check sample's lookup, and Create says why", async ({ page }) => {
  const sent = lookups(page);
  await start(page, /Card access/, "prod");
  await page.getByLabel("Entity id").fill("card_status");
  await page.getByRole("combobox", { name: "Root table" }).selectOption({ label: "access_db · visitor_profile" });
  await page.getByRole("button", { name: "Link by columns…" }).click();
  await page.getByLabel("Column of a location").selectOption("visitor_profile.status");
  await page.getByLabel("New table").selectOption({ label: "access_db · cards" });
  await page.getByLabel("Its column").selectOption("status");
  await page.getByRole("button", { name: "Add the location" }).click();
  await page.getByLabel("Sample", { exact: true }).fill("VP00012931");
  await page.getByLabel("Reason", { exact: true }).selectOption("incident");
  await page.getByLabel("Reference", { exact: true }).fill("ACCESS-311");
  await page.getByRole("button", { name: "Check sample" }).click();
  await expect(lastCheck(page)).toHaveText("Last check: not looked up: a save check fails. Fix it below first.");
  await expect(page.getByRole("region", { name: "Save checks failed for cards" })).toBeVisible();
  expect(sent).toHaveLength(0);
  await page.getByRole("button", { name: "Create card_status" }).click();
  await expect(page.getByLabel("Failed save checks")).toContainText("Table Scan of cards: the lookup would read all of cards.");
  await expect(page.getByRole("button", { name: /^Write / })).toHaveCount(0);
  expect(sent).toHaveLength(0);
});

test("coming back to the tab looks nothing up again", async ({ page }) => {
  const sent = lookups(page);
  await start(page, /Card access/, "uat");
  await page.getByRole("combobox", { name: "Root table" }).selectOption({ label: "access_db · visitor_profile" });
  await page.getByLabel("Sample", { exact: true }).fill("VP00012931");
  await expect(lastCheck(page)).toHaveText("Last check: 1 location: 1 Found.");
  const refetched = page.waitForRequest((r) => r.method() === "GET" && /\/applications\/card_access\/catalog$/.test(r.url()));
  await page.evaluate(() => window.dispatchEvent(new Event("visibilitychange")));
  await refetched; // the catalog is read again; its content hasn't changed
  await page.waitForTimeout(3000);
  expect(sent).toHaveLength(1);
});

test("from 1280 px Follow a key and Identifiers are beside the graph, and below it on narrower screens", async ({ page }) => {
  await start(page, /Card access/, "uat");
  await page.getByRole("combobox", { name: "Root table" }).selectOption({ label: "access_db · visitor_profile" });
  const at = async (width: number) => {
    await page.setViewportSize({ width, height: 900 });
    const of = async (name: string) => {
      const box = await page.getByRole("region", { name, exact: true }).boundingBox();
      if (!box) throw new Error(`${name} is not on the page`);
      return box;
    };
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth), `${width} scrolls sideways`).toBe(true);
    return { graph: await of("Entity graph"), follow: await of("Follow a key"), identifiers: await of("Identifiers") };
  };
  for (const width of [1440, 1280]) {
    const { graph: g, follow, identifiers } = await at(width);
    for (const beside of [follow, identifiers]) expect(beside.x, `${width}`).toBeGreaterThanOrEqual(g.x + g.width);
    expect(Math.abs(follow.y - g.y), `${width}`).toBeLessThan(2);
  }
  // At 1440 a root and what it links to are whole in the graph's frame, their outcomes with them.
  await page.setViewportSize({ width: 1440, height: 900 });
  await start(page, /Wallet/, "uat");
  await page.getByRole("combobox", { name: "Root table" }).selectOption({ label: "wallet_db · topups" });
  await page.getByRole("region", { name: "Follow a key" }).getByRole("button", { name: "Add ledger_entries" }).click();
  await linkWebhooks(page);
  await page.getByLabel("Sample", { exact: true }).fill("tp_91f2c7");
  await expect(graph(page).getByRole("button", { name: "Provider webhooks Missing" })).toBeVisible();
  const clipped = await graph(page).evaluate((group) => {
    const frame = (group.parentElement as HTMLElement).getBoundingClientRect();
    return [...group.querySelectorAll("[data-box]")].filter((b) => b.getBoundingClientRect().right > frame.right).map((b) => b.getAttribute("data-box"));
  });
  expect(clipped).toEqual([]);
  await start(page, /Card access/, "uat");
  await page.getByRole("combobox", { name: "Root table" }).selectOption({ label: "access_db · visitor_profile" });
  for (const width of [1279, 390]) {
    const { graph: g, follow, identifiers } = await at(width);
    for (const below of [follow, identifiers]) expect(below.y, `${width}`).toBeGreaterThanOrEqual(g.y + g.height);
  }
});

test("outside Production too, a draft failing its index check is never looked up", async ({ page }) => {
  const sent = lookups(page);
  await start(page, /Card access/, "uat");
  await page.getByRole("combobox", { name: "Root table" }).selectOption({ label: "access_db · visitor_profile" });
  await page.getByLabel("Sample", { exact: true }).fill("VP00012931");
  await expect(lastCheck(page)).toHaveText("Last check: 1 location: 1 Found.");
  expect(sent).toHaveLength(1);
  // cards by status would scan the table: its plan check refuses it, so the sample is not looked up.
  await page.getByRole("button", { name: "Link by columns…" }).click();
  await page.getByLabel("Column of a location").selectOption("visitor_profile.status");
  await page.getByLabel("New table").selectOption({ label: "access_db · cards" });
  await page.getByLabel("Its column").selectOption("status");
  await page.getByRole("button", { name: "Add the location" }).click();
  await expect(lastCheck(page)).toHaveText("Last check: not looked up: a save check fails. Fix it below first.");
  await page.waitForTimeout(2500); // longer than the checks' and the lookup's waits together
  expect(sent).toHaveLength(1);
  await page.getByRole("button", { name: "Remove cards" }).click();
  await expect(lastCheck(page)).toHaveText("Last check: 1 location: 1 Found.");
});

test("another application starts its own graph: no draft, reason or reference carries over, and it opens outside Production", async ({ page }) => {
  const sent = lookups(page);
  await start(page, /Card access/, "prod");
  await page.getByRole("combobox", { name: "Root table" }).selectOption({ label: "access_db · visitor_profile" });
  await page.getByLabel("Sample", { exact: true }).fill("VP00012931");
  await page.getByLabel("Reason", { exact: true }).selectOption("incident");
  await page.getByLabel("Reference", { exact: true }).fill("ACCESS-311");
  await page.getByRole("button", { name: "Application" }).click();
  await page.getByRole("button", { name: /Wallet/ }).last().click();
  await expect(page.getByRole("banner").getByLabel("Environment")).toHaveValue("uat");
  await expect(page.getByLabel("Entity id")).toHaveValue("");
  await expect(page.getByLabel("Sample", { exact: true })).toHaveValue("");
  await page.getByRole("banner").getByLabel("Environment").selectOption("prod");
  await expect(page.getByLabel("Reason", { exact: true })).toHaveValue("");
  await expect(page.getByLabel("Reference", { exact: true })).toHaveValue("");
  expect(sent).toHaveLength(0);
});

test("a Transaction table read by a column the catalog does not call a key: the link is marked inline, then the entity is created", async ({ page }) => {
  await start(page, /Wallet/, "prod");
  await page.getByLabel("Entity id").fill("wallet_topups");
  await page.getByRole("combobox", { name: "Root table" }).selectOption({ label: "wallet_db · wallets" });
  await page.getByRole("region", { name: "Follow a key" }).getByRole("button", { name: "Add topups" }).click();
  const failed = page.getByRole("region", { name: "Save checks failed for topups" });
  await page.getByRole("button", { name: "Create wallet_topups" }).click(); // Production: the press runs the checks
  await expect(failed).toContainText("Time filter: topups is a Transaction table: filter on its time column created_at");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  // Not looked up while a save check fails, even by hand ...
  await page.getByLabel("Sample", { exact: true }).fill("u_88213");
  await page.getByLabel("Reason", { exact: true }).selectOption("incident");
  await page.getByLabel("Reference", { exact: true }).fill("WALLET-482");
  await page.getByRole("button", { name: "Check sample" }).click();
  await expect(lastCheck(page)).toHaveText("Last check: not looked up: a save check fails. Fix it below first.");
  await failed.getByRole("button", { name: "Mark topups.user_id as a link to wallets.user_id" }).click();
  await expect(failed.getByLabel("Diff of wallet/catalog.yaml")).toContainText("links_to: {connector: wallet_db, table: wallets, column: user_id}");
  await failed.getByRole("button", { name: "Confirm in catalog.yaml" }).click();
  // ... and allowed once the catalog's link is confirmed, though the entity itself didn't change.
  await expect(lastCheck(page)).toHaveText("Last check: Not checked yet.");
  await page.getByRole("button", { name: "Check sample" }).click();
  await expect(lastCheck(page)).toHaveText("Last check: 2 locations: 2 Found.");
  await expect(page.getByText("Every location passes its save checks.")).toBeVisible();
  await page.getByRole("button", { name: "Create wallet_topups" }).click();
  await page.getByRole("button", { name: "Write wallet/entities/wallet_topups.yaml" }).click();
  await expect(page.getByRole("status", { name: "Entity created" })).toContainText("wallet_topups was created");
});

test("an entity id that exists is caught as it is typed", async ({ page }) => {
  await start(page, /Card access/, "uat");
  await page.getByLabel("Entity id").fill("visitor");
  await expect(page.getByRole("alert")).toHaveText("visitor already exists");
  await expect(page.getByLabel("Entity id")).toHaveAttribute("aria-invalid", "true");
  await page.getByRole("combobox", { name: "Root table" }).selectOption({ label: "access_db · visitor_profile" });
  await expect(page.getByRole("link", { name: "Open visitor" })).toHaveAttribute("href", "/probes?new=inspect&entity=visitor");
  await expect(page.getByRole("button", { name: "Create visitor" })).toBeDisabled();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.getByLabel("Entity id").fill("visitor_badges");
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Create visitor_badges" })).toBeEnabled();
});

test("a connector not imported here says how to import it", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("mock-wallet-uat-unimported", "1"));
  await start(page, /Wallet/, "uat");
  await expect(page.getByText("wallet_db has not been imported in uat; run: owl catalog import wallet_db --application wallet --env uat")).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Root table" }).locator("option", { hasText: "ledger_db · ledger_entries" })).toHaveCount(1);
});

test("the Probe editor offers a new entity", async ({ page }) => {
  await page.goto("/probes");
  await expect(page.getByRole("link", { name: "New entity…" })).toHaveAttribute("href", "/probes/new-entity");
});

test("no tables here: the graph offers the environments that have them, not a CLI command (#94)", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("mock-uat-only-imports", "1"));
  await start(page, /Card access/, "prod");
  await expect(page.getByText("No tables imported in Production")).toBeVisible();
  const offered = page.getByRole("group", { name: "Imported elsewhere" });
  await expect(offered).toContainText("Imported in UAT");
  await expect(page.getByText(/owl catalog import/)).toHaveCount(0);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await offered.getByRole("button", { name: "Use UAT" }).click();
  await expect(page.getByRole("banner").getByLabel("Environment")).toHaveValue("uat");
  await expect(page.getByRole("combobox", { name: "Root table" })).toBeVisible();
  await expect(page.getByLabel("Entity id")).toBeVisible();
});

test("no tables in any environment: the import command is still offered", async ({ page }) => {
  await page.goto("/probes/new-entity");
  await page.getByRole("button", { name: "Application" }).click();
  await page.getByRole("button", { name: /Onboarding/ }).last().click();
  await expect(page.getByText(/No tables imported in/)).toBeVisible();
  await expect(page.getByRole("group", { name: "Imported elsewhere" })).toHaveCount(0);
});
