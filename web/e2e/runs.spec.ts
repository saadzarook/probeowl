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
  await expect(rows).toHaveCount(3); // a header and the first page's two runs: its draft run is hidden
  await expect(rows.nth(1)).toContainText("Top-up completed");
  await expect(rows.nth(1)).toContainText("Failed");
  await expect(rows.nth(1)).toContainText("Incident · WALLET-482");
  await expect(row(page, "tp_91f2b9")).toHaveCount(0);
  // A run the kill switch cut short is Stopped, with the reason, never a bare Failed.
  const stopped = row(page, "kill switch");
  await expect(stopped.locator("[data-status]")).toHaveText("Stopped");
  await expect(stopped).toContainText("stopped by the global kill switch: replica on fire");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});

test("draft runs are hidden until Show draft runs is ticked, which the server filters and the URL keeps (#164)", async ({ page }) => {
  const asked: string[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === "/api/runs") asked.push(new URL(request.url()).searchParams.get("drafts") ?? "");
  });
  await openRuns(page);
  const shown = page.getByRole("checkbox", { name: "Show draft runs" });
  await expect(shown).not.toBeChecked();
  await expect(row(page, "tp_91f2b9")).toHaveCount(0);
  await shown.check();
  await expect(row(page, "tp_91f2b9")).toContainText("Draft");
  await expect(page.getByRole("table").getByRole("row")).toHaveCount(4);
  await expect(page).toHaveURL(/[?&]drafts=show/);
  // Asked of the server each time, never filtered in the browser.
  expect(asked).toContain("false");
  expect(asked.at(-1)).toBe("true");
  await page.reload();
  await expect(page.getByRole("checkbox", { name: "Show draft runs" })).toBeChecked();
  await shown.uncheck();
  await expect(row(page, "tp_91f2b9")).toHaveCount(0);
  await expect(page).not.toHaveURL(/drafts=/);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});

test("older runs come with Show older: a caveat in amber, a masked identifier still masked", async ({ page }) => {
  await openRuns(page);
  await page.getByRole("button", { name: "Show older" }).click();
  const late = row(page, "replica lag unknown");
  await expect(late.locator("[data-status]")).toHaveAttribute("data-caveat", "true");
  await expect(row(page, "[masked]").locator("[data-status]")).toHaveText("Ambiguous");
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
