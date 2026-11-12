// Author: @SaadZarook
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

// First run in the browser (#36): with no applications, every screen opens the setup.
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("mock-no-applications", "1"));
});

async function nameIt(page: Page, name: string, owner: string) {
  await page.getByLabel("Name", { exact: true }).fill(name);
  await page.getByLabel("Owning team").fill(owner);
}

test("with no applications, every screen opens first-run setup", async ({ page }) => {
  for (const screen of ["/", "/inspect", "/validate", "/probes", "/tables", "/connectors"]) {
    await page.goto(screen);
    await expect(page).toHaveURL(/\/setup$/);
    await expect(page.getByRole("heading", { name: "Set up ProbeOwl", level: 1 })).toBeVisible();
  }
  await expect(page.getByText("Your first application · owl init does the same from a terminal")).toBeVisible();
  await expect(page.getByRole("link", { name: "Your first application" })).toHaveAttribute("href", "/guide/first-application/");
  await expect(page.getByRole("note")).toContainText("ProbeOwl made its store when it started");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  // A tool needs no application, so it still opens.
  await page.goto("/tools/compare-files");
  await expect(page).toHaveURL(/\/tools\/compare-files$/);
});

test("the id follows the name without environment words, and can be changed", async ({ page }) => {
  await page.goto("/setup");
  await nameIt(page, "Card access UAT", "access-team");
  await expect(page.getByText("Id card_access:")).toBeVisible();
  await page.getByRole("button", { name: "Change the id" }).click();
  const id = page.getByLabel("Id", { exact: true });
  await expect(id).toHaveValue("card_access");
  await id.fill("9cards");
  await expect(page.getByRole("alert")).toContainText("use a-z, 0-9 and _, starting with a letter");
  await expect(page.getByRole("button", { name: "Next: Environments" })).toBeDisabled();
  await id.fill("card_access");
  await expect(page.getByRole("button", { name: "Next: Environments" })).toBeEnabled();
});

test("environments: Dev, UAT and Production suggested, nothing written until Create", async ({ page }) => {
  await page.goto("/setup");
  await nameIt(page, "Card access", "access-team");
  await page.getByRole("button", { name: "Next: Environments" }).click();
  await expect(page.getByLabel(/^Dev/)).toBeChecked();
  await expect(page.getByLabel(/^UAT/)).toBeChecked();
  await expect(page.getByLabel(/^Staging/)).not.toBeChecked();
  await expect(page.getByLabel(/^Production/)).toBeChecked();
  await expect(page.getByText("Nothing is written until Create.")).toContainText("card_access");
  // Create needs a preview of exactly what will be written.
  const create = page.getByRole("button", { name: "Create Card access" });
  await expect(create).toBeDisabled();
  await page.getByLabel(/^Dev/).uncheck();
  await page.getByRole("button", { name: "Preview" }).click();
  const diff = page.getByLabel("Diff of card_access/application.yaml");
  await expect(diff).toContainText("+application: card_access");
  await expect(diff).toContainText("+  prod:");
  await expect(diff).not.toContainText("dev:");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  // A change after the preview needs a new one.
  await page.getByLabel(/^Staging/).check();
  await expect(page.getByText("Edited after this preview.")).toBeVisible();
  await expect(create).toBeDisabled();
  await page.getByLabel(/^Staging/).uncheck();
  await expect(create).toBeEnabled();
  await create.click();

  await expect(page.getByRole("heading", { name: "First connection" })).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: "was created" })).toContainText("Card access was created: card_access/application.yaml");
  await expect(page.getByRole("link", { name: "Add its first connection" })).toHaveAttribute("href", "/connectors");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  // The new application is loaded and chosen.
  await page.getByRole("link", { name: "Later" }).click();
  await expect(page.getByRole("button", { name: "Application" })).toContainText("Card access");
});

test("no environment chosen: Preview waits until one is", async ({ page }) => {
  await page.goto("/setup");
  await nameIt(page, "Ledger", "finance-team");
  await page.getByRole("button", { name: "Next: Environments" }).click();
  for (const env of [/^Dev/, /^UAT/, /^Production/]) await page.getByLabel(env).uncheck();
  await expect(page.getByRole("alert")).toContainText("Choose at least one environment.");
  await expect(page.getByRole("button", { name: "Preview" })).toBeDisabled();
});
