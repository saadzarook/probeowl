// Author: @SaadZarook
import { test } from "@playwright/test";
import { expectNoOverflow } from "./overflow";

// No screen scrolls sideways at common laptop widths (#256): 1152 is a 1440 screen at 125%,
// 1280 a 1920 screen at 150%. The entity graph and an open probe have the longest content.
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
    await page.getByRole("combobox", { name: "Root table" }).selectOption({ label: "access_db · visitor_profile" });
    await page.getByRole("region", { name: "Follow a key" }).getByRole("button", { name: "Add cards" }).click();
    await page.getByLabel("Sample", { exact: true }).fill("VP00012931");
    await page.getByText("Every location passes its save checks.").waitFor();
    await page.getByRole("status", { name: "Last check" }).getByText("2 Found").waitFor();
    await expectNoOverflow(page, `the entity graph at ${width}`);
    await page.getByRole("button", { name: "Create badge_holder_cards" }).click();
    await page.getByLabel("Diff of card-access/entities/badge_holder_cards.yaml").waitFor();
    await expectNoOverflow(page, `the entity graph's Create at ${width}`);
  });
}

for (const width of [320, 390]) {
  test(`setup's environment cards wrap at ${width} px, with a long name (#102)`, async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem("mock-no-applications", "1"));
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/setup");
    await page.getByLabel("Name").fill("Customer onboarding and identity verification platform");
    await page.getByLabel("Owning team").fill("onboarding-team");
    await page.getByRole("button", { name: "Next: Environments" }).click();
    for (const box of await page.getByRole("checkbox").all()) if (!(await box.isChecked())) await box.check();
    await expectNoOverflow(page, `setup's environments at ${width}`);
  });
}
