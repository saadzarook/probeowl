// Author: @SaadZarook
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

// owl serve serves the guide on the web app's own origin.
const GUIDE = "/guide/";

async function chooseApplication(page: Page, name: RegExp) {
  await page.getByRole("button", { name: "Application" }).click();
  await page.getByRole("button", { name }).last().click();
}

test("no Validate probe for the entity offers a new one, which opens as a validate draft", async ({ page }) => {
  await page.goto("/validate");
  await chooseApplication(page, /Card access/);
  const empty = page.getByRole("status").filter({ hasText: "No Validate probes in Card access" });
  await expect(empty).toContainText("A Validate probe checks that a flow ran correctly.");
  await expect(empty.getByRole("link", { name: "Writing probes" })).toHaveAttribute("href", `${GUIDE}writing-probes/`);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await empty.getByRole("link", { name: "New probe" }).click();
  await expect(page).toHaveURL(/\/probes\?new=validate&entity=visitor$/);
  // Named first: the link chose the entity and the kind.
  const form = page.getByRole("form", { name: "New probe" });
  await expect(form.getByLabel("Entity")).toHaveValue("visitor");
  await expect(form.getByLabel("Kind")).toHaveValue("validate");
  await form.getByLabel("Probe id").fill("visitor_badge_issued");
  await form.getByLabel("Title").fill("A visitor's badge was issued");
  await form.getByRole("button", { name: "Start the draft" }).click();
  const yaml = page.getByLabel("Probe YAML");
  await expect(yaml).toContainText("kind: validate");
  await expect(yaml).toContainText("entity: visitor");
});

test("no Inspect probe in an application says so", async ({ page }) => {
  await page.goto("/inspect");
  const empty = page.getByRole("status").filter({ hasText: "No Inspect probes in Wallet" });
  await expect(empty).toContainText("An Inspect probe shows a subject's current state.");
  await expect(empty.getByRole("link", { name: "New probe" })).toHaveAttribute("href", "/probes?new=inspect&entity=topup");
});

test("the Probe editor in an application with no entities says what to add first", async ({ page }) => {
  await page.goto("/probes");
  await chooseApplication(page, /Onboarding/);
  const empty = page.getByRole("region", { name: "Onboarding has no entities yet" });
  await expect(empty).toContainText("onboarding/entities/<name>.yaml");
  await expect(empty.getByRole("link", { name: "Concepts" })).toHaveAttribute("href", `${GUIDE}concepts/`);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});
