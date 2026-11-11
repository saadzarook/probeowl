// Author: @SaadZarook
import { test } from "@playwright/test";
import { expectNoOverflow } from "./overflow";

// No screen scrolls sideways at common laptop widths (#256): 1152 is a 1440 screen at 125%,
// 1280 a 1920 screen at 150%. The wizard's steps and an open probe have the longest content.
const SCREENS = ["/inspect", "/validate", "/probes", "/tables", "/connectors", "/knowledge-base", "/runs"];

for (const width of [1152, 1280, 1440]) {
  test(`nothing scrolls sideways at ${width} px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    for (const path of SCREENS) {
      await page.goto(path);
      await page.getByRole("main").waitFor();
      await expectNoOverflow(page, `${path} at ${width}`);
    }
    await page.goto("/probes");
    await page.getByLabel("Open probe").selectOption("topup_completed");
    await page.getByRole("button", { name: "Save", exact: true }).waitFor();
    await expectNoOverflow(page, `an open probe at ${width}`);

    await page.goto("/probes/new-entity");
    await page.getByRole("button", { name: "Application" }).click();
    await page.getByRole("button", { name: /Card access/ }).last().click();
    await page.getByLabel("Entity id").fill("badge_holder_cards");
    await page.getByRole("button", { name: "Next: Root table" }).click();
    await page.getByRole("combobox", { name: "Root table" }).selectOption({ label: "access_db · visitor_profile" });
    await page.getByRole("button", { name: "Next: Locations" }).click();
    await page.getByRole("region", { name: "Follow a key" }).getByRole("button", { name: "Add cards" }).click();
    await page.getByText("Every location passes its save checks.").waitFor();
    await expectNoOverflow(page, `the wizard's Locations at ${width}`);
    await page.getByRole("button", { name: "Next: Identifiers" }).click();
    await expectNoOverflow(page, `the wizard's Identifiers at ${width}`);
    await page.getByRole("button", { name: "Next: Preview" }).click();
    await expectNoOverflow(page, `the wizard's Preview at ${width}`);
  });
}
