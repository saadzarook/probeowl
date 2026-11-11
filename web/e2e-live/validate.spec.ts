// Author: @SaadZarook
import { expect, test, type Page } from "@playwright/test";

async function validate(page: Page, environment: "uat" | "prod", id: string, reference: string) {
  await page.goto("/validate");
  await page.getByRole("button", { name: "Application" }).click();
  await page.getByRole("button", { name: /Wallet/ }).last().click();
  await expect(page.getByRole("button", { name: "Application" })).toContainText("Wallet");
  await expect(page.getByLabel("Probe")).toHaveValue("topup_completed");
  await page.getByRole("main").getByLabel("Environment").selectOption(environment);
  await page.getByLabel("Any ID").fill(id);
  await page.getByLabel("Reason").selectOption("incident");
  await page.getByLabel(/^Reference/).fill(reference);
  await page.getByRole("button", { name: "Validate" }).click();
}

const result = (page: Page) => page.getByRole("region", { name: "Run result" });

test("tp_91f2c7 in UAT fails: ledger entries Missing", async ({ page }) => {
  await validate(page, "uat", "tp_91f2c7", "WALLET-482");
  await expect(result(page).getByText("Failed", { exact: true })).toBeVisible({ timeout: 60_000 });
  await expect(result(page).getByRole("button", { name: /^Ledger/ })).toContainText("Missing");
  await expect(result(page).getByText(/Ledger entries missing/)).toBeVisible();
  await expect(result(page).getByText("count(ledger) == 2")).toBeVisible();
  await expect(result(page).getByText("0 == 2")).toBeVisible();
});

test("tp_91f2b9 in UAT passes: every location Found", async ({ page }) => {
  await validate(page, "uat", "tp_91f2b9", "WALLET-482");
  await expect(result(page).getByText("Passed", { exact: true })).toBeVisible({ timeout: 60_000 });
  const locations = result(page).locator("ul.locations > li");
  await expect(locations.first()).toBeVisible();
  for (const location of await locations.all()) {
    await expect(location).toContainText("Found");
  }
});

test("tp_93b001 in UAT fails: ledger Blocked by the missing link", async ({ page }) => {
  await validate(page, "uat", "tp_93b001", "WALLET-482");
  await expect(result(page).getByText("Failed", { exact: true })).toBeVisible({ timeout: 60_000 });
  const ledger = result(page).getByRole("button", { name: /^Ledger/ });
  await expect(ledger).toContainText("Blocked");
  await expect(ledger).toContainText("missing link: record.ledger_txn_id is empty");
});

test("Production without a reference is refused before it starts", async ({ page }) => {
  // The form blocks this itself; drop the reference in flight to see the API's own 422.
  await page.route("**/api/runs", async (route) => {
    const body = route.request().postDataJSON() as { investigation: { reference?: string } };
    delete body.investigation.reference;
    await route.continue({ postData: JSON.stringify(body) });
  });
  const refused = page.waitForResponse((r) => r.url().endsWith("/api/runs") && r.request().method() === "POST");
  await validate(page, "prod", "tp_91f2c7", "WALLET-482");
  expect((await refused).status()).toBe(422);
  await expect(page.getByRole("alert")).toContainText("investigation.reference (required when the environment is Production)");
});

// Needs the dev stack's databases: the failing and healthy top-ups are planted there.
test("tp_91f2c7 in UAT compares with its healthy top-up: ledger entries differ", async ({ page }) => {
  await validate(page, "uat", "tp_91f2c7", "WALLET-482");
  await expect(result(page).getByText("Failed", { exact: true })).toBeVisible({ timeout: 60_000 });
  const section = page.getByRole("region", { name: "Compare with a healthy record" });
  await section.getByRole("button", { name: "Newest passing" }).click();
  await expect(section.getByRole("columnheader", { name: "Healthy tp_91f2b9" })).toBeVisible({ timeout: 60_000 });
  const ledger = section.getByRole("row", { name: /^Ledger/ });
  await expect(ledger).toContainText("Missing, 0 rows");
  await expect(ledger).toContainText("Found, 2 rows");
  await expect(section.getByRole("row", { name: /^Amount/ }).first()).toContainText("Expected to vary");
});

// Needs the dev stack's databases, like the comparison above.
test("M4 is done: its differences, a runbook filled in from the run, and a recorded copy", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await validate(page, "uat", "tp_91f2c7", "WALLET-482");
  await expect(result(page).getByText("Failed", { exact: true })).toBeVisible({ timeout: 60_000 });
  const compare = page.getByRole("region", { name: "Compare with a healthy record" });
  await compare.getByRole("button", { name: "Newest passing" }).click();
  await expect(compare.getByRole("row", { name: /^Ledger/ })).toContainText("Different", { timeout: 60_000 });

  const next = page.getByRole("region", { name: "Suggested next steps" });
  await next.getByRole("link", { name: "Open with values" }).first().click();
  const step = (n: number) => page.getByRole("region", { name: new RegExp(`Step ${n}:`) });
  await expect(step(1)).toContainText("aggregate_id = 'tp_91f2c7'");
  await step(1).getByRole("button", { name: "Run read-only" }).click();
  await expect(step(1).getByLabel("Result")).toBeVisible({ timeout: 30_000 });

  const write = step(5);
  await expect(write.getByRole("button", { name: /^Run/ })).toHaveCount(0);
  await write.getByRole("button", { name: "Copy for change request" }).click();
  await expect(write.getByRole("status")).toContainText("recorded in the audit");
  const copied = await page.evaluate(() => navigator.clipboard.readText());
  expect(copied).toContain("-- WRITE · UAT");
  expect(copied).toContain("aggregate_id = 'tp_91f2c7'");
});
