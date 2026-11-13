// Author: @SaadZarook
import { writeFileSync } from "node:fs";
import { expect, test } from "@playwright/test";
import { Counter, entityProbeAndValidate } from "./newcomer";

// The newcomer path on v0.2, measured (#27): `owl serve` in an empty folder, then everything in the
// browser: first-run setup, Add connection on the native MariaDB, Confirm all, and the same entity,
// probe and first Validate as v0.1. dev/measure/newcomer_first_run.sh starts owl serve and names the
// result file, the test-only connector id and the read-only password here. Counting rules:
// newcomer.ts.
const out = process.env["PROBEOWL_MEASURE_OUT"];
const connector = process.env["PROBEOWL_MEASURE_CONNECTOR"];
const password = process.env["PROBEOWL_MEASURE_PASSWORD"];

test.skip(
  out === undefined || process.env["PROBEOWL_MEASURE_PATH"] !== "first-run",
  "measures the v0.2 newcomer path: run dev/measure/newcomer_first_run.sh",
);
// A step that cannot happen fails within a minute, not at the test's own timeout.
test.use({ viewport: { width: 1440, height: 900 }, actionTimeout: 60_000 });

test("owl serve in an empty folder to first Validate: the browser, counted and timed", async ({ page }) => {
  test.setTimeout(600_000);
  if (connector === undefined || password === undefined) throw new Error("name the connector and the password");
  const count = new Counter(page);

  count.start("Open the web app");
  await count.open("/");
  await expect(page).toHaveURL(/\/setup$/);
  count.end();

  // As v0.1's owl init: the card example's application, UAT only.
  count.start("Setup: application");
  await count.fill(page.getByLabel("Name", { exact: true }), "Card baseline");
  await count.fill(page.getByLabel("Owning team"), "access-team");
  await count.click(page.getByRole("button", { name: "Next: Environments" }));
  await count.click(page.getByLabel(/^Dev/));
  await count.click(page.getByLabel(/^Production/));
  await expect(page.getByLabel("Diff of card_baseline/application.yaml")).toBeVisible({ timeout: 60_000 });
  await count.click(page.getByRole("button", { name: "Create Card baseline" }));
  await expect(page.getByRole("status").filter({ hasText: "was created" })).toBeVisible({ timeout: 60_000 });
  count.end();

  // As v0.1's owl init answers: host, database, login, password, and trusting the native server's
  // self-signed certificate, with its reason. Save tests it with the saved password.
  count.start("Setup: connection");
  await count.click(page.getByRole("link", { name: "Add its first connection" }));
  await count.click(page.getByRole("button", { name: /^MariaDB/ }));
  // Not counted: a newcomer keeps the suggested id, card_baseline_mariadb. A test-only id keeps
  // this run's keychain entry apart from any real one (probeowl/probeowl_test_*), and it is
  // deleted after.
  await expect(page.getByLabel("Connector id")).toHaveValue("card_baseline_mariadb");
  await page.getByLabel("Connector id").fill(connector);
  await count.fill(page.getByLabel("Host"), "127.0.0.1");
  await count.fill(page.getByLabel("Database"), "access_uat_test");
  await count.fill(page.getByLabel("Read-only login"), "probeowl_test_ro");
  await count.fillPassword(page.getByLabel("Password"), password);
  await count.select(page.getByLabel("TLS"), "trust");
  await count.fill(page.getByLabel("Why trust it"), "the native test server's own certificate");
  await count.click(page.getByRole("button", { name: "Preview" }));
  await expect(page.getByLabel(`Diff of card_baseline/connectors/${connector}.yaml`)).toBeVisible({ timeout: 60_000 });
  await count.click(page.getByRole("button", { name: `Save ${connector}` }));
  await expect(page.getByRole("status").filter({ hasText: "The password is in this machine's keychain" })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByRole("status").filter({ hasText: "Connected" })).toBeVisible({ timeout: 60_000 });
  count.end();

  count.start("Import and confirm the labels");
  await count.click(page.getByRole("button", { name: `Import ${connector}'s tables` }));
  await count.click(page.getByRole("button", { name: /^Confirm all \d+ as suggested$/ }));
  await expect(page.getByRole("status").filter({ hasText: /^Confirmed \d+ in/ })).toBeVisible({ timeout: 60_000 });
  count.end();

  await entityProbeAndValidate(page, count, connector);
  writeFileSync(out ?? "", JSON.stringify(count.segments, null, 2));
});
