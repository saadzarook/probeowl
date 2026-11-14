// Author: @SaadZarook
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

// Global search, the command palette (#38): Ctrl+K anywhere, / outside a text field, keyboard
// first, each result on its own screen in its own application, and never a silent environment
// change. Masked subjects show as [masked] and match nothing.

const palette = (page: Page) => page.getByRole("dialog");
const box = (page: Page) => palette(page).getByRole("combobox", { name: "Search" });

async function openWithKeys(page: Page) {
  // Once the app is up, so its shortcut listener is.
  await expect(page.getByRole("button", { name: /^Search/ })).toBeVisible();
  await page.keyboard.press("Control+k");
  await expect(box(page)).toBeFocused();
}

async function search(page: Page, text: string) {
  await box(page).fill(text);
  // The palette searches a moment after typing stops.
  await expect(palette(page).getByText("Searching…")).toHaveCount(0);
}

test("Ctrl+K opens it, the arrows and Enter open a result, all from the keyboard", async ({ page }) => {
  await page.goto("/inspect");
  await openWithKeys(page);
  await search(page, "topup_comp");
  const listbox = palette(page).getByRole("listbox", { name: "Results" });
  await expect(listbox.getByRole("group", { name: "Probes" })).toBeVisible();
  const first = listbox.getByRole("option").first();
  await expect(first).toHaveAttribute("aria-selected", "true");
  await expect(box(page)).toHaveAttribute("aria-activedescendant", (await first.getAttribute("id")) ?? "");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.keyboard.press("Enter");
  await expect(palette(page)).toHaveCount(0);
  await expect(page).toHaveURL(/\/probes\?probe=topup_completed$/);
  await expect(page.getByRole("heading", { name: "topup_completed" })).toBeVisible();
});

test("the arrows move through every group, and Escape closes it where it was opened", async ({ page }) => {
  await page.goto("/inspect");
  await page.getByRole("button", { name: /^Search/ }).click();
  await search(page, "top");
  const options = palette(page).getByRole("option");
  const count = await options.count();
  expect(count).toBeGreaterThan(2);
  await page.keyboard.press("ArrowDown");
  await expect(options.nth(1)).toHaveAttribute("aria-selected", "true");
  await page.keyboard.press("ArrowUp");
  await page.keyboard.press("ArrowUp");
  await expect(options.nth(count - 1)).toHaveAttribute("aria-selected", "true"); // wraps around
  await page.keyboard.press("Escape");
  await expect(palette(page)).toHaveCount(0);
  await expect(page.getByRole("button", { name: /^Search/ })).toBeFocused();
});

test("/ opens it outside a text field, and is just a character inside one", async ({ page }) => {
  await page.goto("/inspect");
  await page.getByRole("button", { name: "Application" }).click();
  await page.getByRole("button", { name: /Card access/ }).last().click();
  const field = page.getByLabel("Identifier");
  await field.fill("C2045");
  await field.press("/");
  await expect(field).toHaveValue("C2045/");
  await expect(palette(page)).toHaveCount(0);
  await page.getByRole("heading", { name: "Inspect a subject" }).click();
  await page.keyboard.press("/");
  await expect(box(page)).toBeFocused();
});

test("a masked subject shows as [masked] and searching for it finds no run", async ({ page }) => {
  await page.goto("/runs");
  await openWithKeys(page);
  await search(page, "run_email");
  const runs = palette(page).getByRole("group", { name: "Runs" });
  await expect(runs.getByRole("option")).toHaveCount(1);
  await expect(runs.getByRole("option")).toContainText("[masked]");
  await search(page, "[masked]");
  await expect(palette(page).getByRole("group", { name: "Runs" })).toHaveCount(0);
});

test("a result in another application opens there, and says so when its environment differs", async ({ page }) => {
  await page.goto("/inspect");
  await page.getByRole("button", { name: "Application" }).click();
  await page.getByRole("button", { name: /Onboarding/ }).last().click();
  await page.getByRole("banner").getByLabel("Environment").selectOption("staging");
  await openWithKeys(page);
  await palette(page).getByLabel(/^All applications/).check();
  await search(page, "Wallet");
  await palette(page).getByRole("group", { name: "Applications" }).getByRole("option", { name: /^Wallet/ }).click();
  // Wallet has no Staging: it opens in UAT, its first environment that isn't Production, and says so.
  await expect(page.getByRole("button", { name: "Application" })).toContainText("Wallet");
  await expect(page.getByRole("banner").getByLabel("Environment")).toHaveValue("uat");
  await expect(page.getByRole("status").filter({ hasText: "Wallet has no Staging: showing Wallet UAT." })).toBeVisible();
});

test("an application with nothing but Production opens there, and says it is Production", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("mock-prod-only-app", "1"));
  await page.goto("/inspect");
  await page.getByRole("banner").getByLabel("Environment").selectOption("uat");
  await openWithKeys(page);
  await palette(page).getByLabel(/^All applications/).check();
  await search(page, "Settlements");
  await palette(page).getByRole("option", { name: /^Settlements/ }).click();
  await expect(page.getByRole("banner").getByLabel("Environment")).toHaveValue("prod");
  await expect(
    page.getByRole("status").filter({ hasText: "Settlements has no UAT: showing Settlements Production, which is Production." }),
  ).toBeVisible();
});

test("a result in the same environment opens with no notice, and the view stays in UAT", async ({ page }) => {
  await page.goto("/inspect");
  await page.getByRole("banner").getByLabel("Environment").selectOption("uat");
  await openWithKeys(page);
  await palette(page).getByLabel(/^All applications/).check();
  await search(page, "access_db");
  await palette(page).getByRole("option", { name: /^access_db/ }).click();
  await expect(page).toHaveURL(/\/connectors\?connector=access_db$/);
  await expect(page.getByRole("button", { name: "Application" })).toContainText("Card access");
  await expect(page.getByRole("banner").getByLabel("Environment")).toHaveValue("uat");
  await expect(page.getByRole("region", { name: "Connector access_db" })).toBeVisible();
  await expect(page.locator(".switch-notice")).toHaveCount(0);
});

test("Inspect and Validate with the typed value fill the field, and nothing runs", async ({ page }) => {
  const runs: string[] = [];
  page.on("request", (r) => {
    if (r.method() === "POST" && new URL(r.url()).pathname === "/api/runs") runs.push(r.url());
  });
  await page.goto("/inspect");
  await openWithKeys(page);
  await search(page, "tp_91f2c7");
  await palette(page).getByRole("option", { name: 'Validate "tp_91f2c7"' }).click();
  await expect(page).toHaveURL(/\/validate\?value=tp_91f2c7$/);
  await expect(page.getByLabel("Any ID")).toHaveValue("tp_91f2c7");
  expect(runs).toEqual([]);
});

test("recent items keep only what they are, never a run's subject", async ({ page }) => {
  await page.goto("/runs");
  await openWithKeys(page);
  await search(page, "run_draft");
  await palette(page).getByRole("group", { name: "Runs" }).getByRole("option").first().click();
  await expect(page).toHaveURL(/\/runs\?run=run_draft$/);
  await openWithKeys(page);
  const recent = palette(page).getByRole("group", { name: "Recent" });
  await expect(recent.getByRole("option").first()).toContainText("Run run_draf");
  const stored = await page.evaluate(() => localStorage.getItem("probeowl-search-recent") ?? "");
  expect(stored).toContain('"kind":"run"');
  expect(stored).not.toContain("tp_91f2b9"); // that run's subject
});

test("the guide's pages are searched too", async ({ page }) => {
  await page.goto("/inspect");
  await openWithKeys(page);
  await search(page, "slow to start");
  await expect(palette(page).getByRole("group", { name: "Guide" }).getByRole("option")).toContainText("owl serve is slow to start");
});

test("nothing found, and a search that fails, each say so", async ({ page }) => {
  await page.goto("/inspect");
  await openWithKeys(page);
  await search(page, "zzzz-nothing");
  await expect(palette(page).getByText('Nothing in ProbeOwl matches "zzzz-nothing".')).toBeVisible();
  await search(page, "fail-search");
  const failed = palette(page).getByRole("alert");
  await expect(failed).toContainText("Search could not be run.");
  await expect(failed.getByRole("button", { name: "Retry" })).toBeVisible();
});
