// Author: @SaadZarook
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const RUNBOOK = "Top-up captured but wallet not credited";

async function open(page: Page, query = "") {
  await page.goto(`/knowledge-base${query}`);
  await expect(page.getByRole("heading", { name: "Knowledge base" })).toBeVisible();
}

const step = (page: Page, title: string | RegExp) => page.getByRole("region", { name: title });

test("lists the runbooks and opens the first, with each step labelled", async ({ page }) => {
  await open(page);
  await expect(page.getByRole("button", { name: new RegExp(RUNBOOK) })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("heading", { name: RUNBOOK })).toBeVisible();
  await expect(step(page, /Step 1:/)).toContainText("Read query");
  await expect(step(page, /Step 1:/)).toContainText("reviewed, passes its checks");
  await expect(step(page, /Step 2:/)).toContainText("Read query · reference only");
  await expect(step(page, /Step 2:/)).toContainText("not reviewed yet");
  await expect(step(page, /Step 3:/)).toContainText("External · dynatrace");
  await expect(step(page, /Step 4:/)).toContainText("Manual step");
  await expect(step(page, /Step 5:/)).toContainText("Write query · never run here");
  await expect(step(page, /Step 6:/)).toContainText("Probe");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});

test("a write query never has a run button, with a run or without", async ({ page }) => {
  for (const query of ["", "?run=run_demo"]) {
    await open(page, query);
    const write = step(page, /Step 5:/);
    await expect(write.getByRole("button", { name: "Copy for change request" })).toBeVisible();
    await expect(write.getByRole("button", { name: /run|validate|inspect/i })).toHaveCount(0);
    await expect(write).toContainText("ProbeOwl has no write access and offers no run button");
  }
});

test("a relabelled read says the server treats it as a write, and offers no run", async ({ page }) => {
  await open(page);
  await page.getByRole("button", { name: /Duplicate webhook deliveries/ }).click();
  const write = step(page, /Clear the duplicates/);
  await expect(write).toContainText("Write query · never run here");
  await expect(write).toContainText("Labelled a read query, but it changes data");
  await expect(write.getByRole("button", { name: /^Run/ })).toHaveCount(0);
});

test("with no run, steps cannot run and say how to", async ({ page }) => {
  await open(page);
  await expect(step(page, /Step 1:/).getByRole("button", { name: "Run read-only" })).toHaveCount(0);
  await expect(step(page, /Step 1:/)).toContainText("Open this runbook from a run to run this step.");
  await expect(step(page, /Step 1:/)).toContainText(":topup_id");
  await expect(step(page, /Step 4:/)).toContainText("Open this runbook from a run to tick this step.");
  await expect(step(page, /Step 6:/)).toContainText("Open this runbook from a run to run this probe again.");
});

test("a run's values fill in, and turning them off shows the query as written", async ({ page }) => {
  await open(page, "?run=run_demo");
  await expect(step(page, /Step 1:/)).toContainText("aggregate_id = 'tp_91f2c7'");
  await expect(step(page, /Step 3:/)).toContainText('contains(content, "tp_91f2c7")');
  await expect(step(page, /Step 5:/)).toContainText("-- WRITE · UAT · run run_demo · needs change approval");
  await page.getByLabel("Fill values from run run_demo").uncheck();
  await expect(step(page, /Step 1:/)).toContainText(":topup_id");
  await expect(step(page, /Step 1:/).getByRole("button", { name: "Run read-only" })).toHaveCount(0);
});

test("a reviewed read runs and shows its result", async ({ page }) => {
  await open(page, "?run=run_demo");
  await step(page, /Step 1:/).getByRole("button", { name: "Run read-only" }).click();
  const result = step(page, /Step 1:/).getByLabel("Result");
  await expect(result).toContainText("Found");
  await expect(result).toContainText("failed");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});

test("a reference query offers only a copy", async ({ page }) => {
  await open(page, "?run=run_demo");
  await expect(step(page, /Step 2:/).getByRole("button", { name: "Run read-only" })).toHaveCount(0);
  await expect(step(page, /Step 2:/).getByRole("button", { name: "Copy" })).toBeVisible();
});

test("a full scan is refused and says why", async ({ page }) => {
  await open(page, "?run=run_scan");
  await step(page, /Step 1:/).getByRole("button", { name: "Run read-only" }).click();
  await expect(step(page, /Step 1:/).getByRole("alert")).toContainText("would read all of outbox_events");
  await expect(step(page, /Step 1:/).getByLabel("Run without plan check")).toHaveCount(0);
});

test("an unreadable plan in Production asks for an acknowledgement, then runs", async ({ page }) => {
  await open(page, "?run=run_noplan");
  const read = step(page, /Step 1:/);
  await read.getByRole("button", { name: "Run read-only" }).click();
  await expect(read.getByRole("alert")).toContainText("plan checks could not run");
  await expect(read.getByRole("button", { name: "Run read-only" })).toBeDisabled();
  await read.getByLabel("Run without plan check").check();
  await read.getByRole("button", { name: "Run read-only" }).click();
  await expect(read.getByLabel("Result")).toContainText("Found");
  await expect(read.getByLabel("Result")).toContainText("index use not checked");
});

test("copying a write query records it, and copies what the server wrote", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await open(page, "?run=run_demo");
  const write = step(page, /Step 5:/);
  await expect(write).toContainText("Copying is recorded in the audit");
  await write.getByRole("button", { name: "Copy for change request" }).click();
  await expect(write.getByRole("status")).toHaveText("Copied, and recorded in the audit.");
  const copied = await page.evaluate(() => navigator.clipboard.readText());
  expect(copied).toContain("-- WRITE · UAT · run run_demo · needs change approval");
  expect(copied).toContain("aggregate_id = 'tp_91f2c7'");
});

test("copying a write query with no run leaves its parameters", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await open(page);
  await step(page, /Step 5:/).getByRole("button", { name: "Copy for change request" }).click();
  await expect(step(page, /Step 5:/).getByRole("status")).toContainText("recorded in the audit");
  const copied = await page.evaluate(() => navigator.clipboard.readText());
  expect(copied).toContain("-- WRITE · no run");
  expect(copied).toContain(":topup_id");
});

test("a masked value stays unfilled and says why", async ({ page }) => {
  await open(page, "?run=run_masked");
  await expect(step(page, /Step 2:/).getByLabel("Not filled in")).toContainText(":customer_email: masked in this run");
});

test("a manual step's tick is saved with the run", async ({ page }) => {
  await open(page, "?run=run_demo");
  const tick = step(page, /Step 4:/).getByLabel(/Done · saved with run run_demo/);
  await expect(tick).not.toBeChecked();
  await tick.check();
  await expect(tick).toBeChecked();
  await tick.uncheck();
  await expect(tick).not.toBeChecked();
});

test("a probe step runs the probe again and shows that run", async ({ page }) => {
  await open(page, "?run=run_demo");
  await step(page, /Step 6:/).getByRole("button", { name: "Validate again" }).click();
  await expect(step(page, /Step 6:/).getByRole("status")).toContainText("Run run_");
  await expect(step(page, /Step 6:/).getByRole("status")).toContainText("Failed", { timeout: 10_000 });
});

test("search and the filters narrow the list, and an empty result says so", async ({ page }) => {
  await open(page);
  const list = page.locator(".runbook-list");
  await expect(list.getByRole("button")).toHaveCount(2);
  await page.getByLabel("Search").fill("not credited");
  await expect(list.getByRole("button")).toHaveCount(1);
  await page.getByLabel("Search").fill("nothing like this");
  await expect(page.getByText("No runbook matches. Change the filters above.")).toBeVisible();
  await page.getByLabel("Search").fill("");
  await page.getByRole("combobox", { name: "Table" }).selectOption("provider_webhooks");
  await expect(list.getByRole("button")).toHaveCount(1);
  await expect(list).toContainText("Duplicate webhook deliveries");
  await page.getByRole("combobox", { name: "Table" }).selectOption("");
  await page.getByLabel("Identifier").fill("topup_id");
  await expect(list).toContainText(RUNBOOK);
  await expect(list).not.toContainText("Duplicate webhook deliveries");
});

test("a review that is due and a table that drifted are flagged", async ({ page }) => {
  await open(page);
  const flagged = page.getByRole("button", { name: /Duplicate webhook deliveries/ });
  await expect(flagged).toContainText("Review due");
  await expect(flagged).toContainText("Table drifted");
  await flagged.click();
  await expect(page.getByText("Review due: its last review is over 90 days old.")).toBeVisible();
  await expect(page.getByText("wallet_db.provider_webhooks drifted from the catalog")).toBeVisible();
});

test("the URL says which runbook is open", async ({ page }) => {
  await open(page, "?runbook=duplicate_webhooks");
  await expect(page.getByRole("heading", { name: "Duplicate webhook deliveries" })).toBeVisible();
});

test("an application without runbooks says where one goes, with a minimal one to copy", async ({ page }) => {
  await open(page);
  await page.getByRole("button", { name: "Application" }).click();
  await page.getByRole("button", { name: /Card access/ }).last().click();
  const empty = page.getByRole("region", { name: "No runbooks in this application" });
  await expect(empty).toContainText("card_access/runbooks/");
  // Built from this application's entity and probe, so it loads as it is.
  const example = empty.getByLabel("A minimal runbook");
  await expect(example).toContainText("applies_to: { entity: visitor, when: [profile.missing] }");
  await expect(example).toContainText("probe: visitor_cards");
  await expect(empty.getByRole("button", { name: "Copy runbook" })).toBeVisible();
  await expect(empty.getByRole("link", { name: "Writing runbooks" })).toHaveAttribute("href", "/guide/writing-runbooks/");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});

test("a list that cannot be loaded shows the problem and a retry", async ({ page }) => {
  await open(page);
  await page.getByRole("button", { name: "Application" }).click();
  await page.getByRole("button", { name: /Orders/ }).last().click();
  await expect(page.getByRole("alert").filter({ hasText: "could not be read" })).toBeVisible();
  await page.getByRole("button", { name: "Retry" }).first().click();
  await expect(page.getByText("No runbooks in this application")).toBeVisible();
});

test("steps that cannot be loaded for a run show the problem and a retry", async ({ page }) => {
  await open(page, "?run=run_badsteps");
  await expect(page.getByRole("alert").filter({ hasText: "is for visitor" })).toBeVisible();
  await page.getByRole("button", { name: "Retry" }).first().click();
  await expect(step(page, /Step 1:/)).toContainText("aggregate_id = 'tp_91f2c7'");
});

test("a link to a run opens the run's application", async ({ page }) => {
  await open(page, "?run=run_card");
  await expect(page.getByRole("button", { name: "Application" })).toContainText("Card access");
  await expect(page.getByText("No runbooks in this application")).toBeVisible();
});

test("choosing another application after that stays chosen, and drops the run's values", async ({ page }) => {
  await open(page, "?run=run_card");
  await expect(page.getByRole("button", { name: "Application" })).toContainText("Card access");
  await page.getByRole("button", { name: "Application" }).click();
  await page.getByRole("button", { name: /Wallet/ }).last().click();
  await expect(step(page, /Step 1:/)).toContainText(":topup_id");
  await expect(page.getByRole("button", { name: "Application" })).toContainText("Wallet");
  await expect(page.getByLabel(/Fill values from run/)).toHaveCount(0);
});

test("a link to a run opens the run's environment, and never says Production over UAT", async ({ page }) => {
  await open(page, "?run=run_demo");
  await expect(page.getByLabel("Environment")).toHaveValue("uat");
  await expect(page.getByText("Reason and Reference required")).toHaveCount(0);
  await expect(step(page, /Step 5:/)).toContainText("-- WRITE · UAT");
});
