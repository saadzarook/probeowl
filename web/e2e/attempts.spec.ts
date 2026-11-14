// Author: @SaadZarook
import { expect, test } from "@playwright/test";

// At most 5 connection tests with a typed password per binding in any minute (#61): the sixth
// says "too many attempts, wait a minute", and tries nothing.

test("Add connection's sixth test in a minute says to wait, and tries nothing", async ({ page }) => {
  const tries: string[] = [];
  page.on("response", (r) => {
    if (r.url().endsWith("/connectors/test-draft")) tries.push(`${r.status()} ${r.headers()["retry-after"] ?? ""}`);
  });
  await page.goto("/connectors");
  await page.getByRole("button", { name: "Application" }).click();
  await page.getByRole("button", { name: /Orders/ }).last().click();
  await page.getByLabel("Environment").first().selectOption("uat");
  await page.getByRole("link", { name: "Add connection" }).first().click();
  await page.getByRole("button", { name: /^SQL Server/ }).click();
  await page.getByLabel("Host").fill("db.example.test");
  await page.getByLabel("Database").fill("orders_uat");
  await page.getByLabel("Read-only login").fill("probeowl_ro");
  await page.getByLabel("Password").fill("wrong");
  const test = page.getByRole("button", { name: "Test connection" });
  for (let n = 1; n <= 5; n += 1) {
    await test.click();
    await expect(page.getByRole("alert").filter({ hasText: "authentication failed" })).toBeVisible();
    await expect.poll(() => tries.length).toBe(n);
  }
  await test.click();
  await expect(page.getByRole("alert").filter({ hasText: "too many attempts, wait a minute" })).toBeVisible();
  await expect.poll(() => tries.length).toBe(6);
  expect(tries[5]).toMatch(/^429 \d+$/);
});

test("Change password's test is limited too, and keeps the saved password", async ({ page }) => {
  const puts: string[] = [];
  page.on("request", (r) => {
    if (r.method() === "PUT" && r.url().endsWith("/secret")) puts.push(r.url());
  });
  await page.goto("/connectors");
  await page.getByRole("button", { name: "Application" }).click();
  await page.getByRole("button", { name: /Wallet/ }).last().click();
  await page.getByLabel("Environment").first().selectOption("prod");
  await page.getByRole("button", { name: /^wallet_db/ }).click();
  await page.getByRole("button", { name: "Change password" }).click();
  const prompt = page.getByRole("form", { name: "Change the password for wallet_db in Production" });
  for (let n = 1; n <= 5; n += 1) {
    await prompt.getByLabel("Password for wallet_db", { exact: true }).fill("wrong");
    await prompt.getByRole("button", { name: "Change password" }).click();
    await expect(prompt.getByRole("alert")).toContainText("Not changed: authentication failed");
  }
  await prompt.getByLabel("Password for wallet_db", { exact: true }).fill("wrong");
  await prompt.getByRole("button", { name: "Change password" }).click();
  await expect(prompt.getByRole("alert").filter({ hasText: "too many attempts, wait a minute" })).toBeVisible();
  expect(puts).toEqual([]); // nothing saved over the old one
});
