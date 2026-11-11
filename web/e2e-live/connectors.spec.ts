// Author: @SaadZarook
import { expect, test } from "@playwright/test";

test("wallet in UAT lists its connectors, REST not checked, with no host or login", async ({ page }) => {
  await page.goto("/connectors");
  await page.getByRole("button", { name: "Application" }).click();
  await page.getByRole("button", { name: /Wallet/ }).last().click();
  await expect(page.getByRole("button", { name: "Application" })).toContainText("Wallet");
  await page.getByLabel("Environment").selectOption("uat");
  // Reading each SQL binding can take its whole connect timeout when nothing listens.
  for (const connector of ["wallet_db", "ledger_db", "checkout_api"]) {
    await expect(page.getByRole("row", { name: new RegExp(connector) })).toBeVisible({ timeout: 30_000 });
  }
  await expect(page.getByRole("row", { name: /checkout_api/ })).toContainText("Not checked");
  await expect(page.getByRole("row", { name: /checkout_api/ })).toContainText("5 s · 100 req/min · GET only");
  await expect(page.getByRole("row", { name: /wallet_db/ })).toContainText("2 s · 100 rows");
  // Nothing listens in this run, so the real answer is a fixed reason that names no host.
  await page.getByRole("button", { name: "wallet_db" }).click();
  const detail = page.getByRole("region", { name: "Connector wallet_db" });
  await detail.getByRole("button", { name: "Test connection" }).click();
  await expect(detail.getByRole("status")).toContainText(/Reached in|Not reached/, { timeout: 30_000 });
  const text = await page.locator("body").innerText();
  expect(text).not.toContain("127.0.0.1");
  expect(text).not.toContain("probeowl_ro");
});
