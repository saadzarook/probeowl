// Author: @SaadZarook
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

async function openValidate(page: Page) {
  await page.goto("/validate");
  await expect(page.getByLabel("Probe")).toHaveValue("topup_completed");
}

test("the dropdowns show titles, with the probe's id beside its select", async ({ page }) => {
  await openValidate(page);
  await expect(page.getByLabel("Probe").locator("option:checked")).toHaveText("Top-up completed");
  await expect(page.getByLabel("Entity").locator("option:checked")).toHaveText("Top-up");
  await expect(page.locator(".field-id")).toHaveText("topup_completed");
});

test("Validate stays disabled until the Production record is complete", async ({ page }) => {
  await openValidate(page);
  const validate = page.getByRole("button", { name: "Validate" });
  await expect(page.getByRole("main").getByLabel("Environment")).toHaveValue("prod");
  await expect(validate).toBeDisabled();

  await page.getByLabel("Any ID").fill("tp_91f2c7");
  await expect(validate).toBeDisabled();
  await page.getByLabel("Reason").selectOption("incident");
  await expect(validate).toBeDisabled();
  await page.getByLabel(/^Reference/).fill("WALLET-482");
  await expect(validate).toBeEnabled();

  await page.getByRole("main").getByLabel("Environment").selectOption("uat");
  await page.getByLabel(/^Reference/).fill("");
  await expect(validate).toBeEnabled();
});

test("a time typed with an offset is refused at the field before any run starts", async ({ page }) => {
  await openValidate(page);
  const id = page.getByLabel("Any ID");
  await id.fill("2026-11-07T14:00+04:00");
  await page.getByLabel("Reason").selectOption("incident");
  await page.getByLabel(/^Reference/).fill("WALLET-482");
  await page.getByRole("button", { name: "Validate" }).click();
  await expect(id).toHaveAttribute("aria-invalid", "true");
  await expect(id).toHaveAccessibleDescription(
    "this column stores time without an offset; enter it without +04:00, in the column's own time",
  );
  await expect(page.getByText("topups.created_at is datetime2: this column stores time without an offset")).toBeVisible();
  await expect(page.getByRole("region", { name: "Run result" })).toHaveCount(0);
  const axe = await new AxeBuilder({ page }).include(".validate-form").analyze();
  expect(axe.violations).toEqual([]);
});

test("polls the run, filling in locations, and shows Ledger Missing with its evidence", async ({ page }) => {
  await openValidate(page);
  await page.getByLabel("Any ID").fill("tp_91f2c7");
  await page.getByLabel("Reason").selectOption("incident");
  await page.getByLabel(/^Reference/).fill("WALLET-482");
  await page.getByRole("button", { name: "Validate" }).click();

  const result = page.getByRole("region", { name: "Run result" });
  await expect(result.getByText("Locations are filling in")).toBeVisible();
  await expect(result.getByRole("button", { name: /Top-up record/ })).toBeVisible();
  await expect(result.getByRole("button", { name: /Ledger/ })).toHaveCount(0);

  const ledger = result.getByRole("button", { name: /Ledger/ });
  await expect(ledger).toContainText("Missing");
  // As on the canvas: the connector, the key it was looked up by and where that came from, and the time.
  await expect(ledger).toContainText("ledger_db");
  await expect(ledger.locator(".key-line")).toHaveText("ledger_txn_id = lt_55e1a0 · from record");
  await expect(ledger).toContainText("61 ms");
  await expect(result.getByText("Failed", { exact: true })).toBeVisible();
  await expect(result.getByText(/· WALLET-482 ·/)).toBeVisible();
  await expect(result.getByText(/First failure in dependency order: Ledger entries missing/)).toBeVisible();
  await expect(result.getByText("Fail", { exact: true })).toBeVisible();
  await expect(result.getByText("Skip", { exact: true })).toBeVisible();

  // Titles name the locations; the evidence keeps the id the expressions use.
  await expect(result.getByRole("heading", { name: "Top-up completed on tp_91f2c7" })).toBeVisible();
  await expect(result.getByText(/^owl validate topup tp_91f2c7 --env \w+ --application wallet --probe topup_completed --reason incident --reference WALLET-482$/)).toBeVisible();
  await expect(result.getByRole("button", { name: "Copy command" })).toBeVisible();
  const evidence = result.getByRole("region", { name: "Evidence: ledger" });
  await expect(evidence).toContainText("FROM ledger_entries");
  await expect(evidence).toContainText("0 rows");

  await expect(result.getByText("Compare with a healthy record")).toHaveCount(0);
  const axe = await new AxeBuilder({ page }).analyze();
  expect(axe.violations).toEqual([]);
});

test("a rejected run shows the problem detail", async ({ page }) => {
  await openValidate(page);
  await page.getByLabel("Any ID").fill("nonsense");
  await page.getByLabel("Reason").selectOption("incident");
  await page.getByLabel(/^Reference/).fill("WALLET-482");
  await page.getByRole("button", { name: "Validate" }).click();
  await expect(page.getByRole("alert")).toContainText('No identifier of the entity matches "nonsense".');
});

test("an application without entities shows an empty state", async ({ page }) => {
  await page.goto("/validate");
  await page.getByRole("button", { name: "Application" }).click();
  await page.getByRole("button", { name: /Card access/ }).last().click();
  await expect(page.getByText("Nothing validated yet")).toBeVisible();
  await expect(page.getByRole("button", { name: "Validate" })).toBeDisabled();
});

test("a run skipped by the circuit breaker shows its reason", async ({ page }) => {
  await openValidate(page);
  await page.getByLabel("Any ID").fill("tp_paused");
  await page.getByLabel("Reason").selectOption("incident");
  await page.getByLabel(/^Reference/).fill("WALLET-482");
  await page.getByRole("button", { name: "Validate" }).click();
  await expect(page.getByRole("region", { name: "Run result" }).getByText("Skipped: ledger_db is paused: replica lag 120 s (max 60 s); paused for another 42 s")).toBeVisible();
});

test("a run a kill switch stopped shows why next to Failed, and no first failure", async ({ page }) => {
  await page.goto("/validate");
  await page.getByLabel("Any ID").fill("tp_halted");
  await page.getByLabel("Reason").selectOption("incident");
  await page.getByLabel(/^Reference/).fill("WALLET-482");
  await page.getByRole("button", { name: "Validate" }).click();
  const result = page.getByRole("region", { name: "Run result" });
  await expect(result.getByText("Failed", { exact: true })).toBeVisible();
  await expect(result.getByText("Stopped, not a finding: stopped by the kill switch on connector wallet_db: replica on fire")).toBeVisible();
  await expect(result.getByText(/First failure/)).toHaveCount(0);
});

test("a Missing on a replica whose lag is unknown never looks clean", async ({ page }) => {
  await openValidate(page);
  await page.getByLabel("Any ID").fill("tp_lagunk");
  await page.getByLabel("Reason").selectOption("incident");
  await page.getByLabel(/^Reference/).fill("WALLET-482");
  await page.getByRole("button", { name: "Validate" }).click();
  const result = page.getByRole("region", { name: "Run result" });
  await expect(result.getByText("Failed - replica lag unknown: 1 Missing result may not have arrived yet")).toBeVisible();
  await expect(result.locator(".locations").getByText("Missing - replica lag unknown, the record may not have arrived yet")).toBeVisible();
  await expect(result.getByText("Fail - reads a Missing record that may not have arrived yet: replica lag unknown")).toBeVisible();
  await expect(result.getByText(/First failure in dependency order: Ledger entries missing - replica lag unknown/)).toBeVisible();
  // The clean Missing chip is not shown for it.
  await expect(result.locator('.locations .chip[data-outcome="missing"]:not([data-caveat])')).toHaveCount(0);
  const axe = await new AxeBuilder({ page }).include(".run-result").analyze();
  expect(axe.violations).toEqual([]);
});

async function failedRun(page: Page, id = "tp_91f2c7") {
  await openValidate(page);
  await page.getByLabel("Any ID").fill(id);
  await page.getByLabel("Reason").selectOption("incident");
  await page.getByLabel(/^Reference/).fill("WALLET-482");
  await page.getByRole("button", { name: "Validate" }).click();
  const section = page.getByRole("region", { name: "Compare with a healthy record" });
  await expect(section).toBeVisible();
  return section;
}

test("no comparison is offered until the run has finished on one subject", async ({ page }) => {
  await openValidate(page);
  await page.getByLabel("Any ID").fill("tp_91f2c7");
  await page.getByLabel("Reason").selectOption("incident");
  await page.getByLabel(/^Reference/).fill("WALLET-482");
  await page.getByRole("button", { name: "Validate" }).click();
  await expect(page.getByRole("region", { name: "Run result" }).getByText("Locations are filling in")).toBeVisible();
  await expect(page.getByRole("region", { name: "Compare with a healthy record" })).toHaveCount(0);
});

test("the newest passing record shows its differences, leading with what differs", async ({ page }) => {
  const section = await failedRun(page);
  await expect(section.getByText("under this run's reason and reference")).toBeVisible();
  await expect(section.getByText("Choose how to find a healthy record.")).toBeVisible();
  await section.getByRole("button", { name: "Newest passing" }).click();
  await expect(section.getByRole("status")).toContainText("up to 3 runs");
  await expect(section.getByRole("columnheader", { name: "Healthy tp_91f2b9" })).toBeVisible();
  await expect(section.getByRole("columnheader", { name: "Failing tp_91f2c7" })).toBeVisible();
  const ledger = section.getByRole("row", { name: /^Ledger/ });
  await expect(ledger).toContainText("Missing, 0 rows");
  await expect(ledger).toContainText("Found, 2 rows");
  await expect(ledger).toContainText("Different");
  await expect(section.getByRole("button", { name: "Newest passing" })).toHaveAttribute("aria-pressed", "true");
  await expect(section.getByText("replaced")).toHaveCount(0);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});

test("a field expected to vary is shown, but not as a likely cause", async ({ page }) => {
  const section = await failedRun(page);
  await section.getByRole("button", { name: "Newest passing" }).click();
  const amount = section.getByRole("row", { name: /^Amount/ });
  await expect(amount).toContainText("Expected to vary");
  await expect(amount).toHaveAttribute("data-result", "expected_to_vary");
  const rows = await section.locator("tbody tr").evaluateAll((all) => all.map((row) => row.getAttribute("data-result")));
  expect(rows.lastIndexOf("different")).toBeLessThan(rows.indexOf("expected_to_vary"));
  await expect(section.getByText(/\d+ differences/)).toContainText("5 differences");
});

test("a masked field says only whether it matches", async ({ page }) => {
  const section = await failedRun(page);
  await section.getByRole("button", { name: "Newest passing" }).click();
  const email = section.getByRole("row", { name: /^Customer email/ });
  await expect(email).toContainText("masked");
  await expect(email).toContainText("Different");
  await expect(section.getByText("@")).toHaveCount(0);
});

test("a masked field after a restart says not compared", async ({ page }) => {
  const section = await failedRun(page, "tp_restrt");
  await section.getByRole("button", { name: "Newest passing" }).click();
  const rows = section.getByRole("row", { name: /^Customer email/ });
  await expect(rows.last()).toContainText("Not compared");
});

test("matching and not-compared fields are hidden until asked for", async ({ page }) => {
  const section = await failedRun(page);
  await section.getByRole("button", { name: "Newest passing" }).click();
  await expect(section.getByRole("row", { name: /^Status/ })).toHaveCount(0);
  await expect(section.getByRole("row", { name: /^Direction/ })).toHaveCount(0);
  await section.getByRole("button", { name: "Show all fields" }).click();
  await expect(section.getByRole("row", { name: /^Status/ }).first()).toContainText("Same");
  await expect(section.getByRole("row", { name: /^Direction/ })).toContainText("Not compared");
  await section.getByRole("button", { name: "Hide fields" }).click();
  await expect(section.getByRole("row", { name: /^Status/ })).toHaveCount(0);
});

async function pasted(page: Page, subject: string) {
  const section = await failedRun(page);
  await section.getByRole("button", { name: "Paste an ID" }).click();
  await section.getByLabel("Known-good ID").fill(subject);
  await section.getByRole("button", { name: "Compare", exact: true }).click();
  await expect(section.getByRole("button", { name: "Show all fields" })).toBeVisible();
  return section;
}

test("when every field matched, it says so instead of an empty table", async ({ page }) => {
  const section = await pasted(page, "tp_allsam");
  await expect(section.getByText("All compared fields match")).toBeVisible();
  await expect(section.getByRole("table")).toHaveCount(0);
  await section.getByRole("button", { name: "Show all fields" }).click();
  await expect(section.getByRole("table")).toBeVisible();
  await expect(section.getByText("All compared fields match")).toHaveCount(0);
});

test("when nothing could be compared, it never says the fields match", async ({ page }) => {
  const section = await pasted(page, "tp_allnot");
  await expect(section.getByText("Nothing could be compared")).toBeVisible();
  await expect(section.getByText("All compared fields match")).toHaveCount(0);
  await expect(section.getByRole("table")).toHaveCount(0);
  await section.getByRole("button", { name: "Show all fields" }).click();
  await expect(section.getByRole("row", { name: /^Top-up record/ })).toContainText("Not compared");
});

test("some matching and some not compared says exactly that", async ({ page }) => {
  const section = await pasted(page, "tp_mixedd");
  await expect(section.getByText("Some fields match; the rest could not be compared")).toBeVisible();
  await expect(section.getByText("All compared fields match")).toHaveCount(0);
  await expect(section.getByText("Nothing could be compared")).toHaveCount(0);
});

test("a new comparison says what it replaced", async ({ page }) => {
  const section = await failedRun(page);
  await section.getByRole("button", { name: "Newest passing" }).click();
  await expect(section.getByRole("columnheader", { name: "Healthy tp_91f2b9" })).toBeVisible();
  await section.getByRole("button", { name: "Paste an ID" }).click();
  await section.getByLabel("Known-good ID").fill("tp_8abc12");
  await section.getByRole("button", { name: "Compare", exact: true }).click();
  await expect(section.getByRole("columnheader", { name: "Healthy tp_8abc12" })).toBeVisible();
  await expect(section.getByText("This replaced the earlier comparison with tp_91f2b9. Its runs stay in the audit.")).toBeVisible();
});

test("replacing an attempt that compared nothing says so", async ({ page }) => {
  const section = await failedRun(page);
  await section.getByRole("button", { name: "Same payment method" }).click();
  await expect(section.getByRole("status")).toContainText("none of the 3 candidates passed");
  await section.getByRole("button", { name: "Newest passing" }).click();
  await expect(section.getByRole("columnheader", { name: "Healthy tp_91f2b9" })).toBeVisible();
  await expect(section.getByText("This replaced an earlier attempt, which compared nothing.")).toBeVisible();
});

test("a pasted ID that did not pass is compared with a warning", async ({ page }) => {
  const section = await failedRun(page);
  await section.getByRole("button", { name: "Paste an ID" }).click();
  await section.getByLabel("Known-good ID").fill("tp_93b001");
  await section.getByRole("button", { name: "Compare", exact: true }).click();
  await expect(section.getByRole("alert")).toContainText("tp_93b001 did not pass (failed)");
  // A subject that did not pass is never called healthy.
  await expect(section.getByRole("columnheader", { name: "Did not pass tp_93b001" })).toBeVisible();
  await expect(section.getByText("Healthy tp_93b001")).toHaveCount(0);
  await expect(section.getByText(/^Compared with tp_93b001/)).toBeVisible();
});

test("a pasted ID that is refused says why, and the choices stay open", async ({ page }) => {
  const section = await failedRun(page);
  await section.getByRole("button", { name: "Paste an ID" }).click();
  await expect(section.getByRole("button", { name: "Compare", exact: true })).toBeDisabled();
  await section.getByLabel("Known-good ID").fill("tp_91f2c7");
  await section.getByRole("button", { name: "Compare", exact: true }).click();
  await expect(section.getByRole("alert")).toContainText("that is the subject of this run");
  await expect(section.getByRole("button", { name: "Newest passing" })).toBeEnabled();
});

test("no candidates and a comparison that could not run each say so", async ({ page }) => {
  const none = await failedRun(page, "tp_93b001");
  await none.getByRole("button", { name: "Newest passing" }).click();
  await expect(none.getByRole("status")).toContainText("no candidates; paste a known-good ID");
  await none.getByRole("button", { name: "Paste an ID" }).click();
  await none.getByLabel("Known-good ID").fill("tp_999999");
  await none.getByRole("button", { name: "Compare", exact: true }).click();
  await expect(none.getByRole("alert")).toContainText("could not run: stopped by the kill switch");
});

test("an entity with no sample offers only a pasted ID", async ({ page }) => {
  const section = await failedRun(page, "tp_nosamp");
  await expect(section.getByText("This entity has no healthy sample")).toBeVisible();
  await expect(section.getByRole("button", { name: "Newest passing" })).toHaveCount(0);
  await expect(section.getByRole("button", { name: "Paste an ID" })).toBeVisible();
});

test("a comparison that cannot be read shows the problem and a retry", async ({ page }) => {
  await openValidate(page);
  await page.getByLabel("Any ID").fill("tp_500err");
  await page.getByLabel("Reason").selectOption("incident");
  await page.getByLabel(/^Reference/).fill("WALLET-482");
  await page.getByRole("button", { name: "Validate" }).click();
  const section = page.getByRole("region", { name: "Compare with a healthy record" });
  await expect(section.getByRole("alert")).toContainText("could not be read");
  await section.getByRole("button", { name: "Retry" }).click();
  await expect(section.getByRole("button", { name: "Newest passing" })).toBeVisible();
});

test("a failed run suggests its runbooks, and opening one fills in its values", async ({ page }) => {
  await failedRun(page);
  const next = page.getByRole("region", { name: "Suggested next steps" });
  const first = next.getByRole("listitem").filter({ hasText: "Top-up captured but wallet not credited" });
  await expect(first).toContainText("Matches ledger missing");
  await expect(first).toContainText("6 steps: 2 read, 1 external, 1 manual, 1 write, 1 probe");
  const second = next.getByRole("listitem").filter({ hasText: "Duplicate webhook deliveries" });
  await expect(second).toContainText("review due");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);

  await first.getByRole("link", { name: "Open with values" }).click();
  await expect(page).toHaveURL(/knowledge-base\?runbook=topup_not_credited&run=run_/);
  await expect(page.getByRole("heading", { name: "Top-up captured but wallet not credited" })).toBeVisible();
  await expect(page.getByRole("region", { name: /Step 1:/ })).toContainText("aggregate_id = 'tp_91f2c7'");
  await expect(page.getByRole("region", { name: /Step 5:/ }).getByRole("button", { name: /^Run/ })).toHaveCount(0);
});

test("no runbook to suggest says so", async ({ page }) => {
  await failedRun(page, "tp_nosugg");
  await expect(page.getByRole("region", { name: "Suggested next steps" })).toContainText("No runbook matches this run's outcomes.");
});

test("suggestions that cannot be read show the problem and a retry", async ({ page }) => {
  await failedRun(page, "tp_500sug");
  const next = page.getByRole("region", { name: "Suggested next steps" });
  await expect(next.getByRole("alert")).toContainText("could not be read");
  await next.getByRole("button", { name: "Retry" }).click();
  await expect(next.getByRole("link", { name: "Open with values" }).first()).toBeVisible();
});

test("nothing is suggested until the run has finished", async ({ page }) => {
  await openValidate(page);
  await page.getByLabel("Any ID").fill("tp_91f2c7");
  await page.getByLabel("Reason").selectOption("incident");
  await page.getByLabel(/^Reference/).fill("WALLET-482");
  await page.getByRole("button", { name: "Validate" }).click();
  await expect(page.getByRole("region", { name: "Run result" }).getByText("Locations are filling in")).toBeVisible();
  await expect(page.getByRole("region", { name: "Suggested next steps" })).toHaveCount(0);
});
