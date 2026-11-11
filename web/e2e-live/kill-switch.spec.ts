// Author: @SaadZarook
import { expect, test } from "@playwright/test";

const CONNECTOR = { scope: "connector", application: "wallet", connector: "wallet_db" };
const HEADERS = { "X-ProbeOwl-Request": "1" };

test.setTimeout(120_000);

test("wallet_db's switch: shown on every screen, runs skipped with the reason, then resumed", async ({ page }) => {
  try {
    await page.goto("/connectors");
    await page.getByRole("button", { name: "Application" }).click();
    await page.getByRole("button", { name: /Wallet/ }).last().click();
    await expect(page.getByRole("button", { name: "Application" })).toContainText("Wallet");
    await page.getByLabel("Environment").selectOption("uat");
    await page.getByRole("button", { name: "wallet_db" }).click();

    await page.getByRole("checkbox", { name: "Kill switch" }).click();
    let dialog = page.getByRole("dialog");
    await dialog.getByLabel("Reason").fill("live test");
    await dialog.getByRole("button", { name: "Stop reads" }).click();

    await expect(page.getByRole("row", { name: /wallet_db/ })).toContainText("Stopped", { timeout: 30_000 });
    const banner = page.getByRole("alert", { name: "Kill switches on" });
    await expect(banner).toContainText("connector wallet_db of wallet, in every environment: live test");

    await page.goto("/validate");
    await page.getByRole("button", { name: "Application" }).click();
    await page.getByRole("button", { name: /Wallet/ }).last().click();
    await expect(page.getByRole("alert", { name: "Kill switches on" })).toBeVisible();
    await page.getByRole("main").getByLabel("Environment").selectOption("uat");
    await page.getByLabel("Any ID").fill("tp_91f2b9");
    await page.getByLabel("Reason").selectOption("incident");
    await page.getByRole("button", { name: "Validate" }).click();
    const result = page.getByRole("region", { name: "Run result" });
    await expect(result.getByText("Skipped", { exact: true })).toBeVisible({ timeout: 60_000 });
    await expect(result).toContainText("stopped by the kill switch on connector wallet_db: live test");

    await page.goto("/connectors");
    await page.getByRole("button", { name: "Application" }).click();
    await page.getByRole("button", { name: /Wallet/ }).last().click();
    await page.getByRole("button", { name: "wallet_db" }).click();
    await page.getByRole("checkbox", { name: "Kill switch" }).click();
    dialog = page.getByRole("dialog");
    await dialog.getByLabel("Reason").fill("live test done");
    await dialog.getByRole("button", { name: "Resume reads" }).click();
    await expect(page.getByRole("alert", { name: "Kill switches on" })).toHaveCount(0, { timeout: 30_000 });
  } finally {
    // Never leave it on for the rest of the suite. 404 means it is already off.
    await page.request.post("/api/kill-switches/release", { data: { ...CONNECTOR, reason: "test cleanup" }, headers: HEADERS });
  }
});
