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

test("environments: Dev, UAT and Production suggested, the diff live, nothing written until Create", async ({ page }) => {
  const puts: { base?: string; environments: { name: string }[] }[] = [];
  page.on("request", (r) => {
    if (r.method() === "PUT" && new URL(r.url()).pathname === "/api/applications/card_access") puts.push(r.postDataJSON());
  });
  await page.goto("/setup");
  await nameIt(page, "Card access", "access-team");
  await page.getByRole("button", { name: "Next: Environments" }).click();
  await expect(page.getByLabel(/^Dev/)).toBeChecked();
  await expect(page.getByLabel(/^UAT/)).toBeChecked();
  await expect(page.getByLabel(/^Staging/)).not.toBeChecked();
  await expect(page.getByLabel(/^Production/)).toBeChecked();
  await expect(page.getByText("Nothing is written until Create.")).toContainText("card_access");
  // application.yaml as a diff as soon as the step opens, with no Preview button; it follows
  // each choice.
  await expect(page.getByRole("button", { name: "Preview" })).toHaveCount(0);
  const diff = page.getByLabel("Diff of card_access/application.yaml");
  await expect(diff).toContainText("+application: card_access");
  await expect(diff).toContainText("dev:");
  await page.getByLabel(/^Dev/).uncheck();
  await expect(diff).not.toContainText("dev:");
  await expect(diff).toContainText("+  prod:");
  await page.getByLabel(/^Staging/).check();
  await expect(diff).toContainText("staging:");
  await page.getByLabel(/^Staging/).uncheck();
  await expect(diff).not.toContainText("staging:");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  // The previews wrote nothing; Create is the one write, of exactly the diff shown, by its base.
  expect(puts.length).toBeGreaterThan(0);
  expect(puts.every((p) => p.base === undefined)).toBe(true);
  const create = page.getByRole("button", { name: "Create Card access" });
  await expect(create).toBeEnabled();
  await create.click();

  await expect(page.getByRole("heading", { name: "First connection" })).toBeVisible();
  const written = puts.at(-1);
  expect(written?.base).toEqual(expect.any(String));
  expect(written?.environments.map((e) => e.name)).toEqual(["uat", "prod"]);
  await expect(page.getByRole("status").filter({ hasText: "was created" })).toContainText("Card access was created: card_access/application.yaml");
  await expect(page.getByRole("link", { name: "Add its first connection" })).toHaveAttribute("href", "/connectors/new?environment=uat");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  // The new application is loaded and chosen.
  await page.getByRole("link", { name: "Later" }).click();
  await expect(page.getByRole("button", { name: "Application" })).toContainText("Card access");
});

test("Add its first connection, clicked at once, opens Add connection even while the list is slow", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("mock-slow-applications", "1"));
  await page.goto("/setup");
  await nameIt(page, "Card access", "access-team");
  await page.getByRole("button", { name: "Next: Environments" }).click();
  await page.getByRole("button", { name: "Create Card access" }).click();
  // Shown only once the new application is loaded: before, Add connection would find none and
  // send the newcomer back to setup.
  await page.getByRole("link", { name: "Add its first connection" }).click();
  await expect(page).toHaveURL(/\/connectors\/new\?environment=dev$/); // Dev is kept: the first that isn't Production
  await expect(page.getByRole("heading", { name: "Add connection", level: 1 })).toBeVisible();
});

test("no environment chosen: no diff, and Create waits until one is", async ({ page }) => {
  await page.goto("/setup");
  await nameIt(page, "Ledger", "finance-team");
  await page.getByRole("button", { name: "Next: Environments" }).click();
  for (const env of [/^Dev/, /^UAT/, /^Production/]) await page.getByLabel(env).uncheck();
  await expect(page.getByRole("alert")).toContainText("Choose at least one environment.");
  await expect(page.getByRole("button", { name: "Create Ledger" })).toBeDisabled();
  await expect(page.getByLabel("Diff of ledger/application.yaml")).toHaveCount(0);
});

test.describe("with applications loaded", () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => localStorage.removeItem("mock-no-applications"));
  });

  test("New application in the picker opens setup, for a name not loaded yet", async ({ page }) => {
    await page.goto("/inspect");
    await page.getByRole("button", { name: "Application" }).click();
    await page.getByRole("link", { name: "+ New application" }).click();
    await expect(page).toHaveURL(/\/setup$/);
    await expect(page.getByText("A new application · owl init does the same from a terminal")).toBeVisible();
    await nameIt(page, "Card access", "access-team");
    await expect(page.getByRole("alert")).toContainText("an application named card_access already exists");
    await expect(page.getByRole("button", { name: "Next: Environments" })).toBeDisabled();
    await nameIt(page, "Ledger", "finance-team");
    await expect(page.getByRole("button", { name: "Next: Environments" })).toBeEnabled();
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  });
});
