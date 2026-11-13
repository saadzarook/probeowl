// Author: @SaadZarook
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

// A teammate's machine has no password for a binding: the prompt on Connectors and wherever a run
// needs it, then the run again. And Change password. Same rules as Add connection (#59).
const PASSWORD = "password-prompt-test-pw";

async function choose(page: Page, path: string, application: RegExp, environment: string) {
  await page.goto(path);
  await page.getByRole("button", { name: "Application" }).click();
  await page.getByRole("button", { name: application }).last().click();
  await page.getByLabel("Environment").first().selectOption(environment);
}

/** The URL, both storages and React Query's mutation cache: none may keep a password. */
async function kept(page: Page): Promise<string> {
  return page.evaluate(() => {
    const mutations = (window as unknown as { probeowlMutations?: () => unknown[] }).probeowlMutations?.() ?? ["no hook"];
    return JSON.stringify([location.href, { ...localStorage }, { ...sessionStorage }, mutations]);
  });
}

function carried(page: Page): string[] {
  const seen: string[] = [];
  page.on("request", (r) => {
    if ((r.postData() ?? "").includes(PASSWORD)) seen.push(`${r.method()} ${new URL(r.url()).pathname} ${r.postData() ?? ""}`);
  });
  return seen;
}

test.describe("a binding with no password on this machine", () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem("mock-password-missing", "card_access/access_db/uat"));
  });

  test("Connectors asks for it right there, saves it to the keychain, then tests the connection", async ({ page }) => {
    const sent = carried(page);
    await choose(page, "/connectors", /Card access/, "uat");
    const prompt = page.getByRole("form", { name: "This connection needs your password for UAT" });
    await expect(prompt).toBeVisible();
    await expect(prompt.getByLabel("Password for access_db")).toHaveAttribute("autocomplete", "new-password");
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    await prompt.getByLabel("Password for access_db").fill(PASSWORD);
    await prompt.getByRole("button", { name: "Save password" }).click();
    await expect(page.getByRole("status").filter({ hasText: "Reached in" })).toBeVisible();
    await expect(prompt).toHaveCount(0); // read again: the keychain has it now
    expect(await kept(page)).not.toContain(PASSWORD);
    expect(sent).toHaveLength(1);
    expect(sent[0]).toContain("PUT /api/applications/card_access/connectors/access_db/environments/uat/secret");
    expect(sent[0]).toContain('"replace":false');
  });

  test("a run that needs it is refused before it starts, asks for it there, then runs", async ({ page }) => {
    await choose(page, "/inspect", /Card access/, "uat");
    await page.getByLabel("Identifier").fill("C204518");
    await page.getByLabel("Reason").selectOption("customer_complaint");
    await page.getByRole("button", { name: "Inspect", exact: true }).click();
    const prompt = page.getByRole("form", { name: "This connection needs your password for UAT" });
    await expect(prompt).toBeVisible();
    await expect(page.getByRole("region", { name: "Inspect result" })).toHaveCount(0);
    await prompt.getByLabel("Password for access_db").fill(PASSWORD);
    await prompt.getByRole("button", { name: "Save password" }).click();
    // The same run again, now that the keychain has it.
    await expect(page.getByRole("region", { name: "Inspect result" })).toBeVisible({ timeout: 30_000 });
    await expect(prompt).toHaveCount(0);
    expect(await kept(page)).not.toContain(PASSWORD);
  });
});

test("Validate asks too, and runs again with the same input", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("mock-password-missing", "wallet/wallet_db/uat"));
  const posted: string[] = [];
  page.on("request", (r) => {
    if (r.method() === "POST" && new URL(r.url()).pathname === "/api/runs") posted.push(r.postData() ?? "");
  });
  await page.goto("/validate");
  await expect(page.getByLabel("Probe")).toHaveValue("topup_completed");
  await page.getByRole("main").getByLabel("Environment").selectOption("uat");
  await page.getByLabel("Any ID").fill("tp_91f2b9");
  await page.getByLabel("Reason").selectOption("incident");
  await page.getByRole("button", { name: "Validate" }).click();
  const prompt = page.getByRole("form", { name: "This connection needs your password for UAT" });
  await expect(prompt.locator("xpath=ancestor::form")).toHaveCount(0); // forms never nest
  await prompt.getByLabel("Password for wallet_db", { exact: true }).fill(PASSWORD);
  await prompt.getByRole("button", { name: "Save password" }).click();
  await expect(page.getByRole("region", { name: "Run result" })).toBeVisible({ timeout: 30_000 });
  expect(posted).toHaveLength(2);
  expect(posted[1]).toEqual(posted[0]); // the same run, not a new one typed again
  expect(posted.join()).not.toContain(PASSWORD);
});

test("Run draft asks too, beside its form, and runs the draft once it is saved", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("mock-password-missing", "wallet/wallet_db/uat"));
  await page.goto("/probes");
  await page.getByLabel("Open probe").selectOption("topup_completed");
  await expect(page.getByRole("heading", { name: "topup_completed" })).toBeVisible();
  await page.getByLabel("Environment").first().selectOption("uat");
  await page.getByRole("main").getByLabel("ID", { exact: true }).fill("tp_91f2c7");
  await page.getByLabel("Reason").selectOption("incident");
  await page.getByRole("button", { name: "Run draft" }).click();
  const prompt = page.getByRole("form", { name: "This connection needs your password for UAT" });
  await expect(prompt.locator("xpath=ancestor::form")).toHaveCount(0);
  await prompt.getByLabel("Password for wallet_db", { exact: true }).fill(PASSWORD);
  await prompt.getByRole("button", { name: "Save password" }).click();
  await expect(page.getByRole("form", { name: "Test run" }).getByText("Failed", { exact: true })).toBeVisible();
  expect(await kept(page)).not.toContain(PASSWORD);
});

test("Change password replaces the saved one, then tests the connection", async ({ page }) => {
  const sent = carried(page);
  await choose(page, "/connectors", /Wallet/, "prod");
  await page.getByRole("button", { name: /^wallet_db/ }).click();
  await page.getByRole("button", { name: "Change password" }).click();
  const prompt = page.getByRole("form", { name: "Change the password for wallet_db in Production" });
  await expect(prompt.getByLabel("Password for wallet_db", { exact: true })).toHaveValue("");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await prompt.getByLabel("Password for wallet_db").fill(PASSWORD);
  await prompt.getByRole("button", { name: "Change password" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Reached in" })).toBeVisible();
  await expect(prompt).toHaveCount(0);
  expect(sent).toHaveLength(1);
  expect(sent[0]).toContain('"replace":true');
  expect(await kept(page)).not.toContain(PASSWORD);
  // Opened again, it starts empty: nothing of the last one stays.
  await page.getByRole("button", { name: "Change password" }).click();
  await expect(page.getByLabel("Password for wallet_db", { exact: true })).toHaveValue("");
});
