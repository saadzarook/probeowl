// Author: @SaadZarook
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import { parse } from "yaml";

// First run against a real owl serve started in an empty folder with no store (#36). Its config
// folder is named in PROBEOWL_LIVE_FIRST_RUN_FOLDER; CI's live run serves the examples instead.
const folder = process.env["PROBEOWL_LIVE_FIRST_RUN_FOLDER"];
// verify.sh --first-run names a test-only connector and gives the native MariaDB's read-only password.
const connector = process.env["PROBEOWL_LIVE_TEST_CONNECTOR"];
const password = process.env["PROBEOWL_LIVE_MARIADB_PASSWORD"];

test.skip(folder === undefined, "needs owl serve started in an empty folder: PROBEOWL_LIVE_FIRST_RUN_FOLDER");

test("an empty owl serve opens setup, and Create writes application.yaml and loads it", async ({ page }) => {
  await page.goto("/inspect");
  await expect(page).toHaveURL(/\/setup$/);
  await expect(page.getByRole("note")).toContainText("ProbeOwl made its store when it started");
  await page.getByLabel("Name", { exact: true }).fill("Card access UAT");
  await page.getByLabel("Owning team").fill("access-team");
  await page.getByRole("button", { name: "Next: Environments" }).click();
  await page.getByLabel(/^Dev/).uncheck();
  await page.getByRole("button", { name: "Preview" }).click();
  await expect(page.getByLabel("Diff of card_access/application.yaml")).toContainText("+application: card_access");
  const path = join(folder ?? "", "card_access", "application.yaml");
  expect(existsSync(path)).toBe(false); // nothing written by the preview
  await page.getByRole("button", { name: "Create Card access UAT" }).click();
  await expect(page.getByRole("status").filter({ hasText: "was created" })).toContainText("card_access/application.yaml");

  const written = parse(readFileSync(path, "utf-8")) as Record<string, unknown>;
  expect(written).toEqual({
    application: "card_access",
    title: "Card access UAT",
    owner: "access-team",
    environments: { uat: { title: "UAT", production: false }, prod: { title: "Production", production: true } },
  });
  const loaded = (await (await page.request.get("/api/applications")).json()) as { name: string }[];
  expect(loaded.map((a) => a.name)).toEqual(["card_access"]);
  if (connector === undefined) {
    await page.getByRole("link", { name: "Later" }).click();
    await expect(page.getByRole("button", { name: "Application" })).toContainText("Card access UAT");
    return;
  }

  // Then its first connection, on the native MariaDB, with the password to the real keychain under
  // a test-only connector id (and later a second, <id>_b); verify.sh checks both entries exist,
  // then deletes exactly them, and sweeps any probeowl_test_* a run killed hard left behind.
  await page.getByRole("link", { name: "Add its first connection" }).click();
  await expect(page.getByLabel("Environment").first()).toHaveValue("uat");
  await page.getByRole("button", { name: /^MariaDB/ }).click();
  await page.getByLabel("Connector id").fill(connector);
  await page.getByLabel("Host").fill("127.0.0.1");
  await page.getByLabel("Database").fill("access_uat_test");
  await page.getByLabel("Read-only login").fill("probeowl_test_ro");
  await page.getByLabel("TLS").selectOption("trust");
  await page.getByLabel("Why trust it").fill("the native test server's own certificate");
  // A wrong password first: the fixed reason, never the server's text or the host.
  await page.getByLabel("Password").fill("not-the-password");
  await page.getByRole("button", { name: "Test connection" }).click();
  const refused = page.getByRole("alert").filter({ hasText: "Not connected" });
  await expect(refused).toContainText("authentication failed; check the binding's login and secret", { timeout: 30_000 });
  await expect(refused).not.toContainText("127.0.0.1");
  await page.getByLabel("Password").fill(password ?? "");
  await page.getByRole("button", { name: "Test connection" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Connected" })).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: "Preview" }).click();
  await expect(page.getByLabel(`Diff of card_access/connectors/${connector}.yaml`)).toContainText(`keyring:probeowl/${connector}.uat`);
  await page.getByRole("button", { name: `Save ${connector}` }).click();
  await expect(page.getByRole("status").filter({ hasText: "keychain" })).toContainText("The password is in this machine's keychain", { timeout: 30_000 });
  // Test connection with the saved password: read back from the keychain by owl serve, never by the page.
  await expect(page.getByRole("status").filter({ hasText: "Connected" })).toBeVisible({ timeout: 30_000 });
  const file = readFileSync(join(folder ?? "", "card_access", "connectors", `${connector}.yaml`), "utf-8");
  expect(file).not.toContain(password ?? "");

  // A teammate's file: the same connection under a second test-only id, with nothing in this
  // machine's keychain for it. Connectors asks for its password; saving it connects.
  const teammate = `${connector}_b`;
  writeFileSync(join(folder ?? "", "card_access", "connectors", `${teammate}.yaml`), file.replaceAll(connector, teammate));
  await page.goto("/connectors");
  await page.getByRole("button", { name: "Reload configuration" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Reloaded:" })).toBeVisible();
  await page.getByLabel("Environment").first().selectOption("uat");
  await page.getByRole("button", { name: new RegExp(`^${teammate}`) }).click();
  const missing = page.getByRole("form", { name: "This connection needs your password for UAT" });
  await missing.getByLabel(`Password for ${teammate}`, { exact: true }).fill(password ?? "");
  await missing.getByRole("button", { name: "Save password" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Reached in" })).toBeVisible({ timeout: 30_000 });
  await expect(missing).toHaveCount(0);

  // Change password on the first: a wrong one is saved and fails with the fixed reason, then the
  // right one connects again. The keychain entry is replaced each time, never read by the page.
  await page.getByRole("button", { name: new RegExp(`^${connector}\\b`) }).click();
  for (const [given, expected] of [
    ["not-the-password", "Not reached: authentication failed; check the binding's login and secret"],
    [password ?? "", "Reached in"],
  ] as const) {
    await page.getByRole("button", { name: "Change password" }).click();
    const change = page.getByRole("form", { name: `Change the password for ${connector} in UAT` });
    await change.getByLabel(`Password for ${connector}`, { exact: true }).fill(given);
    await change.getByRole("button", { name: "Change password" }).click();
    await expect(page.getByRole("status").filter({ hasText: expected })).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole("status").filter({ hasText: expected })).not.toContainText("127.0.0.1");
  }
  const stored = (await page.evaluate(() => JSON.stringify([location.href, { ...localStorage }, { ...sessionStorage }]))) as string;
  expect(stored).not.toContain(password ?? "");
});
