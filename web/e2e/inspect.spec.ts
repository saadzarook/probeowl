// Author: @SaadZarook
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

async function openInspect(page: Page) {
  await page.goto("/inspect");
  await page.getByRole("button", { name: "Application" }).click();
  await page.getByRole("button", { name: /Card access/ }).last().click();
  await expect(page.getByLabel("Probe")).toHaveValue("visitor_cards");
}

async function inspect(page: Page, identifier: string) {
  await page.getByLabel("Identifier").fill(identifier);
  await page.getByLabel("Reason").selectOption("customer_complaint");
  await page.getByLabel(/^Reference/).fill("SUP-20931");
  await page.getByRole("button", { name: "Inspect", exact: true }).click();
}

test("a time typed with an offset is refused at the identifier field", async ({ page }) => {
  await openInspect(page);
  await inspect(page, "2026-11-07 14:00 +0400");
  const field = page.getByLabel("Identifier");
  await expect(field).toHaveAttribute("aria-invalid", "true");
  await expect(field).toHaveAccessibleDescription(
    /^this column stores time without an offset; enter it without \+0400, in the column's own time/,
  );
  const axe = await new AxeBuilder({ page }).include(".validate-form").analyze();
  expect(axe.violations).toEqual([]);
});

test("Inspect needs Reason and, in Production, Reference; it shows the detected identifier", async ({ page }) => {
  await openInspect(page);
  const button = page.getByRole("button", { name: "Inspect", exact: true });
  await expect(page.getByRole("main").getByLabel("Environment")).toHaveValue("prod");
  await page.getByLabel("Identifier").fill("C204518");
  await expect(page.getByText("Detected")).toContainText("card_id");
  await expect(button).toBeDisabled();
  await page.getByLabel("Reason").selectOption("customer_complaint");
  await expect(button).toBeDisabled();
  await page.getByLabel(/^Reference/).fill("SUP-20931");
  await expect(button).toBeEnabled();
  await page.getByRole("main").getByLabel("Environment").selectOption("uat");
  await page.getByLabel(/^Reference/).fill("");
  await expect(button).toBeEnabled();
});

test("inspecting C204518 shows the three planted card issues", async ({ page }) => {
  await openInspect(page);
  await inspect(page, "C204518");

  const result = page.getByRole("region", { name: "Inspect result" });
  await expect(result.getByText("Locations are filling in")).toBeVisible();
  await expect(page.getByLabel("Resolution")).toContainText("card_id C204518");
  await expect(page.getByLabel("Resolution")).toContainText("visitor_profile_id VP00012931");
  await expect(page.getByLabel("Resolution")).toContainText("via cards.card_id · 1 match");

  await expect(result.getByText("3 issues")).toBeVisible();
  await expect(result.getByRole("heading", { name: "Visitor cards on VP00012931" })).toBeVisible();
  await expect(result.getByText(/^owl inspect C204518 --env \w+ --application card_access --probe visitor_cards --entity visitor --identifier card_id --reason \w+/)).toBeVisible();
  const table = result.getByRole("table");
  await expect(table.getByRole("row")).toHaveCount(4);
  await expect(table.getByRole("row", { name: /C204518/ })).toContainText("searched record");
  await expect(table.getByRole("row", { name: /C198220/ })).toContainText("valid_to passed 30 days ago, still active");
  await expect(table.getByRole("row", { name: /C177031/ })).toContainText("revoked in database, still opens doors");
  await expect(table.getByRole("columnheader", { name: "Door controller" })).toBeVisible();
  await expect(result.getByText("Fail", { exact: true })).toHaveCount(3);
  await expect(result.getByText("Pass", { exact: true })).toHaveCount(0);

  const axe = await new AxeBuilder({ page }).analyze();
  expect(axe.violations).toEqual([]);
});

test("an email shared by two profiles shows only the Ambiguous list, and the user picks one", async ({ page }) => {
  await openInspect(page);
  await inspect(page, "a.rahman@example.com");

  const ambiguous = page.getByRole("region", { name: "Ambiguous identifier" });
  await expect(ambiguous).toContainText("VP00012931");
  await expect(ambiguous).toContainText("VP00008807");
  await expect(ambiguous).toContainText("[masked]");
  await expect(ambiguous).not.toContainText("a.rahman@example.com");
  await expect(page.getByRole("region", { name: "Inspect result" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Inspect both" })).toBeVisible();

  await ambiguous.getByRole("button", { name: /Inspect this one VP00008807/ }).click();
  await expect(page.getByRole("heading", { name: "Visitor cards on VP00008807" })).toBeVisible();
  await expect(page.getByText("picked from run")).toBeVisible();
  await expect(page.getByRole("region", { name: "Ambiguous identifier" })).toHaveCount(0);
});

test("Inspect both runs the two matches together", async ({ page }) => {
  await openInspect(page);
  await inspect(page, "a.rahman@example.com");
  await page.getByRole("button", { name: "Inspect both" }).click();
  await expect(page.getByRole("heading", { name: "Visitor cards on VP00012931" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Visitor cards on VP00008807" })).toBeVisible();
});

test("a rejected identifier shows the problem detail", async ({ page }) => {
  await openInspect(page);
  await inspect(page, "nonsense");
  await expect(page.getByRole("alert")).toContainText('No identifier of the entity matches "nonsense".');
  await expect(page.getByRole("alert")).toContainText("identifier (Pick which identifier this value is.)");
});

test("a Production run shows the replica lag warning as a notice, apart from the status", async ({ page }) => {
  await openInspect(page);
  await inspect(page, "C204518");
  const result = page.getByRole("region", { name: "Inspect result" });
  const warnings = result.getByRole("list", { name: "Warnings" });
  await expect(warnings.getByRole("listitem")).toHaveText([
    "Replica lag unknown for access_db (missing REPLICA MONITOR): Missing results can't be confirmed fresh",
  ]);
  await expect(result.getByText("3 issues")).toBeVisible();
});

test("a run a kill switch stopped shows why next to Failed", async ({ page }) => {
  await openInspect(page);
  await inspect(page, "VP00000043");
  const result = page.getByRole("region", { name: "Inspect result" });
  await expect(result.getByText("Failed", { exact: true })).toBeVisible();
  await expect(result.getByText("Stopped, not a finding: stopped by the global kill switch: replica on fire")).toBeVisible();
});

test("a run skipped by the circuit breaker shows its reason", async ({ page }) => {
  await openInspect(page);
  await inspect(page, "VP00000042");
  await expect(page.getByRole("region", { name: "Inspect result" }).getByText("Skipped: access_db is paused: replica lag 120 s (max 60 s); paused for another 42 s")).toBeVisible();
});

test("a finished inspect run can be compared with a healthy record too", async ({ page }) => {
  await openInspect(page);
  await inspect(page, "VP00012931");
  const section = page.getByRole("region", { name: "Compare with a healthy record" });
  await expect(section.getByRole("button", { name: "Paste an ID" })).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});
