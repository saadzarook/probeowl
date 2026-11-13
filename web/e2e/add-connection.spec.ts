// Author: @SaadZarook
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

// Add connection (#36): the form, the pasted string read in the browser, Test connection on a
// draft, the save, the password to the keychain, then the import with Confirm all.
const PASSWORD = "add-connection-test-pw";

async function open(page: Page, application: RegExp, environment: string) {
  await page.goto("/connectors");
  await page.getByRole("button", { name: "Application" }).click();
  await page.getByRole("button", { name: application }).last().click();
  await page.getByLabel("Environment").first().selectOption(environment);
  await page.getByRole("link", { name: "Add connection" }).first().click();
  await expect(page).toHaveURL(/\/connectors\/new/);
}

/** Every request body that carried the password, by path: only Test connection and the keychain may. */
function watchPassword(page: Page): string[] {
  const seen: string[] = [];
  page.on("request", (r) => {
    if ((r.postData() ?? "").includes(PASSWORD)) seen.push(new URL(r.url()).pathname);
  });
  return seen;
}

test("a pasted JDBC string fills the fields, moves its password, and is never kept", async ({ page }) => {
  await open(page, /Orders/, "uat");
  const pasted = page.getByLabel("Paste a connection string (optional)");
  await pasted.fill(`jdbc:mariadb://db.example.test:3306/orders_uat?user=probeowl_ro&password=${PASSWORD}`);
  await pasted.press("Enter");
  await expect(pasted).toHaveValue("");
  await expect(page.getByRole("status").filter({ hasText: "Filled from the connection string" })).toContainText(
    "its password moved to the password field. The string itself isn't kept or sent.",
  );
  await expect(page.getByRole("button", { name: /^MariaDB/ })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByLabel("Host")).toHaveValue("db.example.test");
  await expect(page.getByLabel("Port")).toHaveValue("3306");
  await expect(page.getByLabel("Database")).toHaveValue("orders_uat");
  await expect(page.getByLabel("Read-only login")).toHaveValue("probeowl_ro");
  await expect(page.getByLabel("Password")).toHaveValue(PASSWORD);
  await expect(page.getByLabel("Connector id")).toHaveValue("orders_mariadb");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});

test("Test connection on the draft says the fixed reason, then connects", async ({ page }) => {
  await open(page, /Orders/, "uat");
  await page.getByRole("button", { name: /^SQL Server/ }).click();
  await page.getByLabel("Host").fill("db.example.test");
  await page.getByLabel("Database").fill("orders_uat");
  await page.getByLabel("Read-only login").fill("probeowl_ro");
  await page.getByLabel("Password").fill("wrong");
  await page.getByRole("button", { name: "Test connection" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Not connected" })).toContainText("authentication failed; check the binding's login and secret");
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Test connection" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Connected" })).toContainText("Connected in 31 ms");
});

test("Production needs a replica, and trusting a certificate needs a reason", async ({ page }) => {
  await open(page, /Orders/, "prod");
  await page.getByRole("button", { name: /^MariaDB/ }).click();
  await page.getByLabel("Host").fill("replica.example.test");
  await page.getByLabel("Database").fill("orders");
  await page.getByLabel("Read-only login").fill("probeowl_ro");
  await page.getByLabel("Password").fill(PASSWORD);
  await expect(page.getByRole("alert")).toContainText("ProbeOwl reads Production only from a read replica");
  await expect(page.getByRole("button", { name: "Preview" })).toBeDisabled();
  await page.getByLabel(/This is a read replica/).check();
  await page.getByLabel("TLS").selectOption("trust");
  await expect(page.getByRole("button", { name: "Preview" })).toBeDisabled();
  await page.getByLabel("Why trust it").fill("internal CA, not in this machine's store");
  await expect(page.getByRole("button", { name: "Preview" })).toBeEnabled();
  // SQL Server is always encrypted: no "No TLS" there.
  await page.getByRole("button", { name: /^SQL Server/ }).click();
  await expect(page.getByLabel("TLS").locator("option")).toHaveText(["Verify the certificate", "Trust this server's certificate"]);
});

test("saved: the file has the keychain name, the password goes only to the keychain, then import and Confirm all", async ({ page }) => {
  const carried = watchPassword(page);
  await open(page, /Orders/, "uat");
  await page.getByRole("button", { name: /^MariaDB/ }).click();
  await page.getByLabel("Host").fill("db.example.test");
  await page.getByLabel("Database").fill("orders_uat");
  await page.getByLabel("Read-only login").fill("probeowl_ro");
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Test connection" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Connected" })).toBeVisible();
  await page.getByRole("button", { name: "Preview" }).click();
  const diff = page.getByLabel("Diff of orders/connectors/orders_mariadb.yaml");
  await expect(diff).toContainText("secret: keyring:probeowl/orders_mariadb.uat");
  await expect(diff).not.toContainText(PASSWORD);
  await page.getByRole("button", { name: "Save orders_mariadb" }).click();

  await expect(page.getByRole("heading", { name: "orders_mariadb is connected in UAT" })).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: "Written to" })).toContainText("orders/connectors/orders_mariadb.yaml");
  await expect(page.getByRole("status").filter({ hasText: "keychain" })).toContainText("The password is in this machine's keychain");
  await expect(page.getByRole("status").filter({ hasText: "Connected" })).toBeVisible();
  await page.getByRole("button", { name: "Import orders_mariadb's tables" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Imported" })).toContainText("Imported 2 tables");
  await expect(page.getByRole("listitem").filter({ hasText: "visitor_profile" })).toContainText("Master");
  await expect(page.getByRole("listitem").filter({ hasText: "cards" })).toContainText("Transaction");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.getByRole("button", { name: "Confirm all 2 as suggested" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Confirmed" })).toContainText("Confirmed 2 in orders/catalog.yaml");
  // The password went to Test connection and to the keychain, and nowhere else.
  expect([...new Set(carried)].sort()).toEqual([
    "/api/applications/orders/connectors/orders_mariadb/environments/uat/secret",
    "/api/applications/orders/connectors/test-draft",
  ]);
});

test("a password already saved under the name is overwritten only when asked", async ({ page }) => {
  await open(page, /Wallet/, "prod");
  await page.getByRole("button", { name: /^SQL Server/ }).click();
  await page.getByLabel("Connector id").fill("wallet_db");
  await page.getByLabel("Host").fill("replica.example.test");
  await page.getByLabel("Database").fill("wallet");
  await page.getByLabel("Read-only login").fill("probeowl_ro");
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByLabel(/This is a read replica/).check();
  await page.getByRole("button", { name: "Preview" }).click();
  await page.getByRole("button", { name: "Save wallet_db" }).click();
  const question = page.getByRole("alertdialog");
  await expect(question).toContainText("A password is already saved for wallet_db in Production");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await question.getByRole("button", { name: "Overwrite it" }).click();
  await expect(page.getByRole("status").filter({ hasText: "keychain" })).toContainText("The password is in this machine's keychain");
});

test("a link that names an environment, as first run's does, chooses it in the top bar", async ({ page }) => {
  await page.goto("/connectors");
  await page.getByRole("button", { name: "Application" }).click();
  await page.getByRole("button", { name: /Orders/ }).last().click();
  await expect(page.getByLabel("Environment").first()).toHaveValue("prod");
  await page.goto("/connectors/new?environment=uat");
  await expect(page.getByLabel("Environment").first()).toHaveValue("uat");
  await expect(page.getByText("the environment in the top bar")).toContainText("For UAT");
});

// --- Where a password may not end up (#57's follow-up) --------------------------------------

async function fillDraft(page: Page) {
  await open(page, /Orders/, "uat");
  await page.getByRole("button", { name: /^MariaDB/ }).click();
  await page.getByLabel("Host").fill("db.example.test");
  await page.getByLabel("Database").fill("orders_uat");
  await page.getByLabel("Read-only login").fill("probeowl_ro");
  await page.getByLabel("Password").fill(PASSWORD);
}

/** Everything the page keeps beyond its inputs: the URL, both storages, and React Query's mutation cache. */
async function kept(page: Page): Promise<string> {
  return page.evaluate(() => {
    const mutations = (window as unknown as { probeowlMutations?: () => unknown[] }).probeowlMutations?.() ?? ["no hook"];
    return JSON.stringify([location.href, { ...localStorage }, { ...sessionStorage }, mutations]);
  });
}

test("the password is never in the URL, either storage, or the mutation cache, and is gone after leaving", async ({ page }) => {
  await fillDraft(page);
  await page.getByRole("button", { name: "Test connection" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Connected" })).toBeVisible();
  expect(await kept(page)).not.toContain(PASSWORD);
  expect(await kept(page)).not.toContain("no hook"); // the hook is there, so the cache was read
  // Leave the form, then come back: nothing of it survives.
  await page.getByRole("link", { name: "Inspect", exact: true }).click();
  expect(await kept(page)).not.toContain(PASSWORD);
  await page.goBack();
  await expect(page.getByRole("heading", { name: "Add connection" })).toBeVisible();
  await page.getByRole("button", { name: /^MariaDB/ }).click();
  await expect(page.getByLabel("Password")).toHaveValue("");
});

test("a pasted string never sits in the field: pasted, read, cleared, the password in its own field", async ({ page }) => {
  await open(page, /Orders/, "uat");
  const field = page.getByLabel("Paste a connection string (optional)");
  // A real paste event: the string is read from the clipboard data and never put in the field.
  await field.evaluate((input, text) => {
    const data = new DataTransfer();
    data.setData("text", text);
    input.dispatchEvent(new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true }));
  }, `mariadb://probeowl_ro:${PASSWORD}@db.example.test:3306/orders_uat`);
  await expect(page.getByLabel("Password")).toHaveValue(PASSWORD);
  await expect(field).toHaveValue("");
  // One that can't be read is cleared too, and says nothing was kept.
  await field.fill(`not a connection string ${PASSWORD}`);
  await field.press("Enter");
  await expect(field).toHaveValue("");
  await expect(page.getByRole("status").filter({ hasText: "Nothing was kept" })).toBeVisible();
});

test("password inputs ask the browser not to fill them, and start empty", async ({ page }) => {
  await open(page, /Orders/, "uat");
  await page.getByRole("button", { name: /^SQL Server/ }).click();
  await expect(page.getByLabel("Password")).toHaveAttribute("autocomplete", "new-password");
  await expect(page.getByLabel("Password")).toHaveValue("");
  await page.getByRole("button", { name: /^REST API/ }).click();
  await page.getByLabel("It needs a token").check();
  await expect(page.getByLabel("Token", { exact: true })).toHaveAttribute("autocomplete", "new-password");
  await expect(page.getByLabel("Token", { exact: true })).toHaveValue("");
});
