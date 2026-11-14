// Author: @SaadZarook
import { writeFileSync } from "node:fs";
import { expect, test } from "@playwright/test";
import { Counter, entityProbeAndValidate } from "./newcomer";

// The newcomer path's browser half on v0.1, measured (#27): from the first page of `owl serve`
// after `owl init` to a first Validate on the card example. dev/measure/newcomer_baseline.sh serves
// the folder owl init wrote, with its schema imported, and names the result file here. The counting
// rules are in newcomer.ts; the v0.2 path, from first-run setup, is newcomer-first-run.spec.ts.
const out = process.env["PROBEOWL_MEASURE_OUT"];

test.skip(out === undefined || process.env["PROBEOWL_MEASURE_PATH"] === "first-run", "measures the v0.1 newcomer path: run dev/measure/newcomer_baseline.sh");
// A step that cannot happen fails within a minute, not at the test's own timeout.
test.use({ viewport: { width: 1440, height: 900 }, actionTimeout: 60_000 });

test("install to first Validate: the browser half, counted and timed", async ({ page }) => {
  test.setTimeout(600_000);
  const count = new Counter(page);

  count.start("Open the web app");
  await count.open("/");
  await expect(page.getByRole("button", { name: "Application" })).toContainText("Card baseline");
  count.end();

  // owl init imported the schema; each table's suggested label is confirmed as it stands.
  count.start("Confirm the table labels");
  await count.click(page.getByRole("link", { name: "Tables" }));
  for (const table of ["visitor_profile", "cards"]) {
    await count.click(page.getByRole("button", { name: new RegExp(`^${table}`) }));
    const detail = page.getByRole("region", { name: `Table ${table}` });
    await count.click(detail.getByRole("button", { name: "Preview" }));
    await expect(detail.getByLabel("Diff of card_baseline/catalog.yaml")).toBeVisible({ timeout: 60_000 });
    await count.click(detail.getByRole("button", { name: "Confirm" }));
    await expect(detail.getByRole("status").filter({ hasText: "Written to" })).toBeVisible({ timeout: 60_000 });
  }
  count.end();

  await entityProbeAndValidate(page, count, "card_baseline_mariadb");
  writeFileSync(out ?? "", JSON.stringify(count.segments, null, 2));
});
