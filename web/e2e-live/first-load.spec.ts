// Author: @SaadZarook
import { expect, test, type Page } from "@playwright/test";

// What a newcomer's browser sends before doing anything: every screen, for each application and
// environment, against a real owl serve. None of these reads a probed database.
const SCREENS = ["/inspect", "/validate", "/probes", "/tables", "/connectors", "/knowledge-base", "/runs"];
const APPLICATIONS = ["Wallet", "Card access"];

/** Every /api response the page receives from now on, as "status METHOD path". */
function recordApi(page: Page): string[] {
  const seen: string[] = [];
  page.on("response", (response) => {
    const url = new URL(response.url());
    if (url.pathname.startsWith("/api/")) seen.push(`${response.status()} ${response.request().method()} ${url.pathname}${url.search}`);
  });
  return seen;
}

async function chooseApplication(page: Page, title: string) {
  await page.getByRole("button", { name: "Application" }).click();
  await page.getByRole("button", { name: new RegExp(title) }).last().click();
  await expect(page.getByRole("button", { name: "Application" })).toContainText(title);
}

/** The probe dropdown's choices on a form screen, once its probes have loaded. */
async function probeChoices(page: Page, screen: string, application: string, environment: string): Promise<string[]> {
  await page.goto(screen);
  await chooseApplication(page, application);
  await page.locator("#environment").selectOption(environment);
  await page.waitForLoadState("networkidle");
  return page.getByLabel("Probe").locator("option").allTextContents();
}

test("every request on first load, on every screen, in each application and environment, is 2xx", async ({ page }) => {
  const seen = recordApi(page);
  for (const screen of SCREENS) {
    await page.goto(screen);
    await page.waitForLoadState("networkidle");
    for (const application of APPLICATIONS) {
      await chooseApplication(page, application);
      for (const environment of ["prod", "uat"]) {
        await page.locator("#environment").selectOption(environment);
        await page.waitForLoadState("networkidle");
      }
    }
  }
  expect(seen.length).toBeGreaterThan(0);
  expect(seen.filter((line) => !line.startsWith("2"))).toEqual([]);
});

test("the top bar shows the person running owl serve, as runs record them", async ({ page }) => {
  await page.goto("/inspect");
  const me = (await (await page.request.get("/api/me")).json()) as { name: string };
  expect(me.name).not.toBe("");
  await expect(page.getByLabel(`Signed in as ${me.name}`)).toContainText(me.name);
  // Not the designs' placeholder, unless that really is who runs it.
  if (me.name !== "s.khan") await expect(page.getByText("s.khan")).toHaveCount(0);
});

test("the Validate dropdown lists Wallet's validate probe in UAT and Production", async ({ page }) => {
  for (const environment of ["uat", "prod"]) {
    expect(await probeChoices(page, "/validate", "Wallet", environment)).toEqual(["Top-up completed"]);
  }
});

test("the Inspect dropdown lists Card access's inspect probe in UAT and Production", async ({ page }) => {
  for (const environment of ["uat", "prod"]) {
    expect(await probeChoices(page, "/inspect", "Card access", environment)).toEqual(["Visitor cards"]);
  }
});

test("an application with no Validate probe says so, and where to write one", async ({ page }) => {
  expect(await probeChoices(page, "/validate", "Card access", "uat")).toEqual([]);
  await expect(page.getByRole("status").filter({ hasText: "No Validate probes in Card access" })).toBeVisible();
  await expect(page.getByRole("link", { name: "New probe" })).toHaveAttribute("href", "/probes?new=validate&entity=visitor");
});

// Before tables.spec.ts in the one worker's file order, so access_db has no import in UAT yet.
test("a schema never imported is an empty state with the command to import it, not an error", async ({ page }) => {
  const seen = recordApi(page);
  await page.goto("/tables");
  await chooseApplication(page, "Card access");
  await page.locator("#environment").selectOption("uat");
  await page.waitForLoadState("networkidle");
  await expect(page.getByText("owl catalog import access_db --application card_access --env uat").first()).toBeVisible();
  expect(seen.filter((line) => line.includes("/catalog/schema"))).toEqual([]);
});
