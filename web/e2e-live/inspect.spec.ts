// Author: @SaadZarook
import { expect, test, type Page } from "@playwright/test";

// It names the privilege MariaDB refused: SLAVE MONITOR on 11.8, REPLICA MONITOR on some others, the same one.
const REPLICA_WARNING = /Replica lag unknown for access_db \(missing (REPLICA|SLAVE) MONITOR\): Missing results can't be confirmed fresh/;

/** Card access in Production: the form needs Reason and Reference before it starts. */
async function inspect(page: Page, identifier: string) {
  await page.goto("/inspect");
  await page.getByRole("button", { name: "Application" }).click();
  await page.getByRole("button", { name: /Card access/ }).last().click();
  await expect(page.getByRole("button", { name: "Application" })).toContainText("Card access");
  await expect(page.getByLabel("Probe")).toHaveValue("visitor_cards");
  await expect(page.getByRole("main").getByLabel("Environment")).toHaveValue("prod");
  await page.getByLabel("Identifier").fill(identifier);
  await page.getByLabel("Reason").selectOption("customer_complaint");
  await page.getByLabel(/^Reference/).fill("SUP-20931");
  await page.getByRole("button", { name: "Inspect", exact: true }).click();
}

test("C204518 in Production resolves to VP00012931 and fails its three expectations", async ({ page }) => {
  await inspect(page, "C204518");

  const result = page.getByRole("region", { name: "Inspect result" });
  await expect(result.locator(".status")).toHaveAttribute("data-status", "failed", { timeout: 60_000 });
  await expect(page.getByText("Detected")).toContainText("card_id");
  const resolution = page.getByLabel("Resolution");
  await expect(resolution).toContainText("card_id C204518");
  await expect(resolution).toContainText("visitor_profile_id VP00012931");
  await expect(resolution).toContainText("via cards.card_id");

  const table = result.getByRole("table");
  const headers = await table.getByRole("columnheader").allTextContents();
  const controller = headers.indexOf("Door controller");
  expect(controller).toBeGreaterThanOrEqual(0);
  const cards = table.getByRole("row").filter({ has: page.getByRole("cell") });
  await expect(cards).toHaveCount(3);
  for (const card of await cards.all()) {
    await expect(card.getByRole("cell").nth(controller)).not.toHaveText("—");
  }

  await expect(result.getByText("Fail", { exact: true })).toHaveCount(3);
  await expect(result.getByText("2 <= 1")).toBeVisible();
  await expect(result.getByText("fails for C198220")).toBeVisible();
  await expect(result.getByText("fails for C177031")).toBeVisible();
  await expect(result.getByRole("list", { name: "Warnings" })).toContainText(REPLICA_WARNING);
});

test("a shared email is Ambiguous and masked; picking a match starts a run from it", async ({ page }) => {
  const email = "contractor.one@example.com";
  const started = page.waitForResponse((r) => r.url().endsWith("/api/runs") && r.request().method() === "POST");
  await inspect(page, email);
  const ambiguousRunId = ((await (await started).json()) as { id: string }).id;

  const ambiguous = page.getByRole("region", { name: "Ambiguous identifier" });
  await expect(ambiguous).toBeVisible({ timeout: 60_000 });
  await expect(ambiguous).toContainText("Ambiguous");
  await expect(ambiguous).toContainText("VP00012931");
  await expect(ambiguous).toContainText("VP00008807");
  await expect(ambiguous).toContainText("[masked]");
  await expect(page.getByText(email)).toHaveCount(0);
  await expect(page.getByRole("region", { name: "Inspect result" })).toHaveCount(0);

  const picked = page.waitForResponse(async (r) => {
    if (!/\/api\/runs\/[^/]+$/.test(r.url()) || r.request().method() !== "GET") return false;
    return ((await r.json()) as { from_run?: string | null }).from_run === ambiguousRunId;
  });
  await ambiguous.getByRole("button", { name: /Inspect this one VP00012931/ }).click();
  await picked;
  await expect(page.getByRole("heading", { name: "Visitor cards on VP00012931" })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText(`picked from run ${ambiguousRunId}`)).toBeVisible();
});
