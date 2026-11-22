// Author: @SaadZarook
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

// A connector with no binding in the environment (#25): outside Production the run says which checks
// were not applicable, and stays Passed; in Production it is refused. SHOTS=<folder> saves the note
// at 1440 and 390, light and dark, for the PR.

const SHOTS = process.env["SHOTS"];
const NOTE = "1 check not applicable in UAT: door_controller_api has no binding here";

async function inspectIn(page: Page, environment: string, value: string) {
  await page.goto("/inspect");
  await page.getByRole("button", { name: "Application" }).click();
  await page.getByRole("button", { name: /Card access/ }).last().click();
  await expect(page.getByLabel("Probe")).toHaveValue("visitor_cards");
  await page.getByRole("main").getByLabel("Environment").selectOption(environment);
  await page.getByLabel("Identifier").fill(value);
  await page.getByLabel("Reason").selectOption("customer_complaint");
  await page.getByLabel(/^Reference/).fill("SUP-20931");
  await page.getByRole("button", { name: "Inspect", exact: true }).click();
}

for (const theme of ["light", "dark"] as const) {
  for (const width of [1440, 390]) {
    test(`outside Production the run is Passed and notes the check not applicable, ${theme} ${width}`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: theme });
      await page.setViewportSize({ width, height: 900 });
      await inspectIn(page, "uat", "VP00031207");
      const result = page.getByRole("region", { name: "Inspect result" });
      await expect(result.getByRole("heading", { name: "Visitor cards on VP00031207" })).toBeVisible();
      const note = result.getByRole("note", { name: "Not applicable" });
      await expect(note).toContainText(NOTE);
      await expect(note).toContainText("--strict");
      // The check reads as Not applicable, never as a plain Skip or a failure.
      const check = result.getByRole("listitem").filter({ hasText: "all(cards, controller.status == status)" });
      await expect(check.locator("[data-not-applicable]")).toHaveText("N/ANot applicable");
      // Shown, but never highlighted as a fault: the card's row is not marked red.
      await expect(result.locator("[data-highlight]").filter({ hasText: "C210447" })).toHaveCount(0);
      await expect(result).toContainText("door_controller_api has no binding here");
      const axe = await new AxeBuilder({ page }).include("main").analyze();
      expect(axe.violations).toEqual([]);
      if (SHOTS) await page.screenshot({ path: `${SHOTS}/not-applicable-${theme}-${width}.png`, fullPage: true });
    });
  }
}

test("in Production the same visitor is refused, naming the location that isn't bound", async ({ page }) => {
  await inspectIn(page, "prod", "VP00031207");
  const alert = page.getByRole("alert");
  await expect(alert).toContainText("cards.controller (door_controller_api) has no binding here");
  await expect(alert).toContainText("in Production every location must be bound");
  await expect(page.getByRole("region", { name: "Inspect result" })).toHaveCount(0);
});
