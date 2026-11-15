// Author: @SaadZarook
import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

// The Audit page (#79): read-only, newest first, filtered, masked as owl serve sends it.

test("the audit lists who did what, where, newest first, with masked details", async ({ page }) => {
  await page.goto("/audit");
  await expect(page.getByRole("heading", { level: 1, name: "Audit" })).toBeVisible();
  const rows = page.getByRole("region", { name: "Audit events" }).locator("tbody tr");
  await expect(rows).toHaveCount(50);
  await expect(rows.first()).toContainText("s.khan via MCP");
  await expect(rows.first()).toContainText("reason=production_refused");
  await expect(rows.nth(1)).toContainText('identifiers={"email":"[masked]"}');
  await expect(rows.nth(1).getByRole("link", { name: "Open run" })).toHaveAttribute("href", "/runs?run=run_card");
  await expect(rows.nth(2)).toContainText("reason=authentication failed");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  // Read-only: the page's own buttons are the filters' Show and paging's Show older, nothing else.
  const own = page.getByRole("main").getByRole("button");
  await expect(own).toHaveText(["Show", "Show older"]);
  await page.getByRole("button", { name: "Show older" }).click();
  await expect(rows).toHaveCount(53);
  await expect(page.getByRole("button", { name: "Show older" })).toHaveCount(0);
});

test("each filter narrows the list, and the person an agent acted for finds its events", async ({ page }) => {
  await page.goto("/audit");
  const filters = page.getByRole("form", { name: "Filter the audit" });
  const rows = page.getByRole("region", { name: "Audit events" }).locator("tbody tr");
  await filters.getByLabel("Action").fill("connection_tested");
  await filters.getByRole("button", { name: "Show" }).click();
  await expect(rows).toHaveCount(1);
  await filters.getByLabel("Action").fill("");
  await filters.getByLabel("Application").selectOption("Card access");
  await filters.getByLabel("Environment").selectOption("Production");
  await filters.getByRole("button", { name: "Show" }).click();
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText("mcp_tool_called");
  await filters.getByLabel("Environment").selectOption("All environments");
  await filters.getByLabel("Application").selectOption("All applications");
  await filters.getByLabel("Actor").fill("s.khan");
  await filters.getByRole("button", { name: "Show" }).click();
  await expect(rows.first()).toContainText("s.khan via MCP");
});

test("no match says so, and how to see more", async ({ page }) => {
  await page.goto("/audit");
  const filters = page.getByRole("form", { name: "Filter the audit" });
  await filters.getByLabel("Actor").fill("nobody");
  await filters.getByRole("button", { name: "Show" }).click();
  await expect(page.getByText("No events match these filters")).toBeVisible();
  await expect(page.getByText(/Clear one to see more/)).toBeVisible();
});

test("an audit that cannot be read shows the problem and a retry", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("mock-audit-error", "1"));
  await page.goto("/audit");
  // After the query's own retries, as every list in the app.
  await expect(page.getByRole("alert")).toContainText("The store could not be read.", { timeout: 20_000 });
  await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();
});
