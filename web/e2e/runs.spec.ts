// Author: @SaadZarook
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

async function openRuns(page: Page) {
  await page.goto("/runs");
  await page.getByLabel("Environment").first().selectOption("uat");
}

const row = (page: Page, text: string | RegExp) => page.getByRole("row").filter({ hasText: text });

test("Wallet's runs in UAT, newest first, each row as the list shows it", async ({ page }) => {
  await openRuns(page);
  const rows = page.getByRole("table").getByRole("row");
  await expect(rows).toHaveCount(4); // a header and the first page of three
  await expect(rows.nth(1)).toContainText("Top-up completed");
  await expect(rows.nth(1)).toContainText("Failed");
  await expect(rows.nth(1)).toContainText("Incident · WALLET-482");
  await expect(row(page, "tp_91f2b9")).toContainText("Draft");
  // A run the kill switch cut short is Stopped, with the reason, never a bare Failed.
  const stopped = row(page, "kill switch");
  await expect(stopped.locator(".status")).toHaveText("Stopped");
  await expect(stopped).toContainText("stopped by the global kill switch: replica on fire");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});

test("older runs come with Show older: a caveat in amber, a masked identifier still masked", async ({ page }) => {
  await openRuns(page);
  await page.getByRole("button", { name: "Show older" }).click();
  const late = row(page, "replica lag unknown");
  await expect(late.locator(".status")).toHaveAttribute("data-caveat", "true");
  await expect(row(page, "[masked]").locator(".status")).toHaveText("Ambiguous");
  await expect(page.getByRole("button", { name: "Show older" })).toHaveCount(0);
});

test("a run opens in its result view, with a way back to the list", async ({ page }) => {
  await openRuns(page);
  await row(page, "WALLET-482").first().getByRole("link", { name: "Top-up completed" }).click();
  await expect(page).toHaveURL(/\/runs\?run=run_demo$/);
  await expect(page.getByRole("heading", { name: "Top-up completed on tp_91f2c7" })).toBeVisible();
  await page.getByRole("link", { name: "← All runs" }).click();
  await expect(page.getByRole("table")).toBeVisible();
});

test("an inspect run opens in the Inspect result view", async ({ page }) => {
  await page.goto("/runs?run=run_card");
  await expect(page.getByRole("region", { name: "Inspect result" })).toBeVisible();
});

test("an environment with no runs says so and where to start one", async ({ page }) => {
  await page.goto("/runs");
  await page.getByRole("button", { name: "Application" }).click();
  await page.getByRole("button", { name: /Card access/ }).last().click();
  const empty = page.getByRole("region", { name: /^No runs yet in Card access/ });
  await expect(empty.getByRole("link", { name: "Validate" })).toHaveAttribute("href", "/validate");
});
