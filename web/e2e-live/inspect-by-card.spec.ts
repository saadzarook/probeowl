// Author: @SaadZarook
import { expect, test } from "@playwright/test";
import { expectNoOverflow } from "../e2e/overflow";

// The retest's blocker (#253) on a real MariaDB: the wizard's entity has no identifier patterns,
// and C204518 is a card ID. The run must try each identifier, find the card, and inspect its
// visitor; not read it as a visitor_profile_id and come back Missing. dev/native/newcomer_live.sh
// writes the visitor_cards probe and runs this after the wizard's spec.
const folder = process.env["PROBEOWL_LIVE_NEWCOMER_FOLDER"];

test.skip(folder === undefined, "needs the newcomer's folder: run dev/native/newcomer_live.sh");
test.use({ viewport: { width: 1152, height: 900 } });

test("a card ID with no patterns is tried as each identifier and finds its visitor", async ({ page }) => {
  test.setTimeout(120_000);
  await page.goto("/inspect");
  await page.getByRole("button", { name: "Application" }).click();
  await page.getByRole("button", { name: /Access uat test/ }).last().click();
  await page.getByLabel("Identifier").fill("C204518");
  await expect(page.getByText(/No pattern places this value: each identifier is tried, visitor_profile_id first/)).toBeVisible();
  await expect(page.getByLabel("Find by")).toHaveValue("");
  await page.getByLabel("Reason").selectOption("incident");
  await page.getByRole("button", { name: "Inspect", exact: true }).click();

  // It said which identifier matched, and the probe ran on the visitor that card belongs to.
  const resolution = page.getByLabel("Resolution");
  await expect(resolution).toContainText("card_id C204518", { timeout: 60_000 });
  await expect(resolution).toContainText("visitor_profile_id VP00012931");
  await expect(page.getByText(/so each was tried .*: it is a card_id/)).toBeVisible();
  const result = page.getByRole("region", { name: "Inspect result" });
  await expect(result.getByRole("heading", { name: "A visitor's cards on VP00012931" })).toBeVisible();
  await expect(result.getByRole("heading", { name: /^Visitor profile/ })).toBeVisible();
  await expect(result).not.toContainText("Missing");
  await expect(result.getByRole("row", { name: /^C204518 .*searched record/ })).toBeVisible();
  await expectNoOverflow(page, "the Inspect result");
  // The command to run it again names the identifier that matched.
  await expect(result.getByText(/owl inspect C204518 .*--identifier card_id/)).toBeVisible();

  // The Probe editor with the newcomer's probe open fits too.
  await page.goto("/probes");
  await page.getByLabel("Open probe").selectOption("visitor_cards");
  await expect(page.getByRole("button", { name: "Save", exact: true })).toBeVisible();
  await expectNoOverflow(page, "the Probe editor with visitor_cards open");
});
