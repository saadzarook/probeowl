// Author: @SaadZarook
import { expect, test } from "@playwright/test";

test("importing wallet_db in UAT lists topups with its real columns", async ({ page }) => {
  await page.goto("/tables");
  await page.getByRole("button", { name: "Application" }).click();
  await page.getByRole("button", { name: /Wallet/ }).last().click();
  await expect(page.getByRole("button", { name: "Application" })).toContainText("Wallet");
  await page.getByLabel("Environment").selectOption("uat");
  await expect(page.getByRole("row", { name: /topups/ })).toBeVisible();

  await page.getByRole("button", { name: "Import from replica" }).first().click();
  const dialog = page.getByRole("dialog", { name: "Import from replica" });
  await dialog.getByLabel("SQL connector").selectOption("wallet_db");
  await dialog.getByRole("button", { name: "Import", exact: true }).click();
  await expect(dialog.getByText(/Imported \d+ tables? from wallet_db/)).toBeVisible({ timeout: 90_000 });
  await dialog.getByRole("button", { name: "Close" }).click();

  const detail = page.getByRole("region", { name: /Table .*topups/ });
  for (const column of ["id", "status", "created_at", "provider_payment_id", "ledger_txn_id"]) {
    await expect(detail.getByRole("listitem").filter({ has: page.getByText(column, { exact: true }) })).toContainText(/\w+(\(\d+(,\d+)?\))?/);
  }
  await expect(detail.getByRole("listitem").filter({ has: page.getByText("status", { exact: true }) })).toContainText("Status");

  // the review suggests a label for every imported table
  await expect(page.getByRole("row", { name: /wallet_db/ }).filter({ hasText: "no label" })).toHaveCount(0);
  await expect(page.getByRole("row", { name: /wallet_db/ }).filter({ hasText: "suggested" }).first()).toBeVisible();
});
