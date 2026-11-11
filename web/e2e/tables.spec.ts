// Author: @SaadZarook
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

async function openTables(page: Page) {
  await page.goto("/tables");
  await expect(page.getByRole("heading", { name: "Tables" })).toBeVisible();
  await expect(page.getByRole("row", { name: /topups/ })).toBeVisible();
}

test("lists the wallet catalog with labels, time columns and rows", async ({ page }) => {
  await openTables(page);
  const topups = page.getByRole("row", { name: /topups/ });
  await expect(topups).toContainText("wallet_db · SQL Server");
  await expect(topups).toContainText("Transaction");
  await expect(topups).toContainText("created_at");
  await expect(topups).toContainText("~48 M");
  await expect(page.getByRole("row", { name: /provider_webhooks/ })).toContainText("received_at · 30 d");
  await expect(page.getByRole("row", { name: /ledger_entries/ })).toContainText("~400 M");
  await expect(page.getByRole("row", { name: /ledger_entries/ })).toContainText("ledger_db · Oracle");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});

test("filters by label with counts", async ({ page }) => {
  await openTables(page);
  await expect(page.getByRole("button", { name: "All 8" })).toBeVisible();
  await page.getByRole("button", { name: "Transaction 2" }).click();
  await expect(page.getByRole("row", { name: /topups/ })).toBeVisible();
  await expect(page.getByRole("row", { name: /ledger_entries/ })).toBeVisible();
  await expect(page.getByRole("row", { name: /outbox_events/ })).toHaveCount(0);
  await page.getByRole("button", { name: "Outbox 1" }).click();
  await expect(page.getByRole("row", { name: /outbox_events/ })).toBeVisible();
  await expect(page.getByRole("row", { name: /topups/ })).toHaveCount(0);
  // suggested labels count too
  await page.getByRole("button", { name: "Master 1" }).click();
  await expect(page.getByRole("row", { name: /wallets/ })).toContainText("suggested");
});

test("topups shows imported types merged with its roles", async ({ page }) => {
  await openTables(page);
  const detail = page.getByRole("region", { name: "Table topups" });
  await expect(detail.getByLabel("Owner")).toHaveValue("payments-team");
  await expect(detail.getByLabel("Description")).toHaveValue("One row per top-up attempt.");
  // What confirming the label really changes: the time filter save check.
  await expect(detail).toContainText("What confirming Transaction does");
  await expect(detail).toContainText("every lookup on it must use a key or a time window from a parameter, or saving refuses it");
  const column = (name: string) => detail.getByRole("listitem").filter({ has: page.locator("code").getByText(name, { exact: true }) });
  await expect(column("id")).toContainText("varchar(32)");
  const checked = (name: string, role: string) => column(name).getByRole("checkbox", { name: role, exact: true });
  await expect(checked("id", "Primary key")).toBeChecked();
  await expect(column("amount")).toContainText("decimal(18,2)");
  await expect(checked("ledger_txn_id", "Link")).toBeChecked();
  // The link's target is picked from the imported tables, on any connector.
  const target = column("ledger_txn_id").getByRole("group", { name: "Link target of ledger_txn_id" });
  await expect(target.getByLabel("Table")).toHaveValue("ledger_db.ledger_entries");
  await expect(target.getByLabel("Column")).toHaveValue("txn_id");
  await expect(checked("provider_payment_id", "External key")).toBeChecked();
  await expect(column("status").getByLabel("Values of status")).toHaveValue("pending, succeeded, failed");
  await expect(column("created_at").getByRole("checkbox", { name: "Indexed" })).toBeChecked();
  await expect(checked("customer_email", "PII")).toBeChecked();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});

test("a suggested link comes with its target, picked from the imported tables; Other types one", async ({ page }) => {
  await openTables(page);
  await page.getByRole("button", { name: /topup_status_history/ }).click();
  const detail = page.getByRole("region", { name: "Table topup_status_history" });
  const column = (name: string) => detail.getByRole("listitem").filter({ has: page.locator("code").getByText(name, { exact: true }) });
  await expect(column("topup_id").getByRole("checkbox", { name: "Link", exact: true })).toBeChecked();
  // topup_id is named for topups' key: the import suggests where it points.
  const target = column("topup_id").getByRole("group", { name: "Link target of topup_id" });
  await expect(target.getByLabel("Table")).toHaveValue("wallet_db.topups");
  await expect(target.getByLabel("Column")).toHaveValue("id");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await detail.getByRole("button", { name: "Preview" }).click();
  await expect(detail.getByLabel("Diff of wallet/catalog.yaml")).toContainText("links_to: {connector: wallet_db, table: topups, column: id}");

  // Another table: its key is picked first, and any column can be chosen.
  await target.getByLabel("Table").selectOption("ledger_db.ledger_entries");
  await expect(target.getByLabel("Column")).toHaveValue("txn_id");
  // A table not imported here is typed.
  await target.getByLabel("Table").selectOption("other");
  await target.getByLabel("Target of topup_id").fill("archive_db.old_topups.id");
  await detail.getByRole("button", { name: "Preview" }).click();
  await expect(detail.getByLabel("Diff of wallet/catalog.yaml")).toContainText("links_to: {connector: archive_db, table: old_topups, column: id}");
});

test("imports wallet_db in UAT from the button", async ({ page }) => {
  await openTables(page);
  await page.getByLabel("Environment").selectOption("uat");
  const detail = page.getByRole("region", { name: "Table topups" });
  await expect(detail.getByRole("heading", { name: "No schema imported for wallet_db in UAT" })).toBeVisible();
  await expect(detail.getByText("owl catalog import wallet_db --application wallet --env uat", { exact: true })).toBeVisible();
  await expect(detail.getByRole("button", { name: "Copy command" })).toBeVisible();
  await expect(detail).toContainText("only the marked columns are shown");
  await expect(detail).not.toContainText("varchar(32)");

  await page.getByRole("button", { name: "Import from replica" }).first().click();
  const dialog = page.getByRole("dialog", { name: "Import from replica" });
  await expect(dialog.getByLabel("SQL connector")).toHaveValue("wallet_db");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await dialog.getByRole("button", { name: "Import", exact: true }).click();
  await expect(dialog.getByRole("button", { name: "Importing…" })).toBeDisabled();
  await expect(dialog.getByRole("status")).toContainText("Importing wallet_db");
  await expect(dialog.getByRole("status")).toContainText("Imported 7 tables from wallet_db in UAT.");
  await dialog.getByRole("button", { name: "Close" }).click();
  await expect(detail).toContainText("varchar(32)");
});

test("shows an unreachable database and a truncated import as given", async ({ page }) => {
  await openTables(page);
  await page.getByLabel("Environment").selectOption("uat");
  await page.getByRole("button", { name: "Import from replica" }).first().click();
  const dialog = page.getByRole("dialog", { name: "Import from replica" });
  await dialog.getByLabel("SQL connector").selectOption("ledger_db");
  await dialog.getByRole("button", { name: "Import", exact: true }).click();
  await expect(dialog.getByRole("alert")).toContainText("ledger_db could not be read");
  await dialog.getByRole("button", { name: "Cancel" }).click();

  await page.getByRole("button", { name: "Application" }).click();
  await page.getByRole("button", { name: /Card access/ }).last().click();
  await expect(page.getByRole("row", { name: /visitor_profile/ })).toBeVisible();
  await page.getByRole("button", { name: "Import from replica" }).first().click();
  await page.getByRole("button", { name: "Import", exact: true }).click();
  await expect(page.getByRole("dialog").getByRole("status")).toContainText("imported 1000 of 1,240 tables");
});

test("an application with nothing imported and no catalog says to import, with the button", async ({ page }) => {
  await page.goto("/tables");
  await page.getByRole("button", { name: "Application" }).click();
  await page.getByRole("button", { name: /Orders/ }).last().click();
  const empty = page.getByRole("region", { name: "No tables yet in Orders" });
  await expect(empty).toContainText("Confirming a label writes it to the application's catalog.yaml.");
  await empty.getByRole("button", { name: "Import from replica" }).click();
  await expect(page.getByRole("dialog", { name: "Import from replica" })).toBeVisible();
});

test("imported tables are listed before any catalog.yaml, and confirming one creates the file", async ({ page }) => {
  // As owl init leaves an application: its schema imported, no catalog.yaml yet.
  await page.addInitScript(() => localStorage.setItem("mock-no-catalog", "1"));
  await page.goto("/tables");
  await expect(page.getByRole("row", { name: /topups/ })).toBeVisible();
  await expect(page.getByRole("row", { name: /wallets/ })).toContainText("suggested");
  await expect(page.getByRole("status", { name: "Needs review" })).toContainText("suggested labels not yet confirmed");
  await page.getByRole("button", { name: /wallets/ }).click();
  const detail = page.getByRole("region", { name: "Table wallets" });
  await detail.getByRole("button", { name: "Preview" }).click();
  await expect(detail).toContainText("wallet/catalog.yaml");
  await detail.getByRole("button", { name: "Confirm" }).click();
  await expect(detail).toContainText("Written to wallet/catalog.yaml. Review and commit it with git.");
  await expect(page.getByRole("row", { name: /wallets/ })).not.toContainText("suggested");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});

test("shows the review banner, suggestions, usage and drift", async ({ page }) => {
  await openTables(page);
  await expect(page.getByRole("status", { name: "Needs review" })).toContainText(
    "4 suggested labels not yet confirmed · 1 table differs from the live schema (UAT)",
  );
  const currencies = page.getByRole("row", { name: /currencies/ });
  await expect(currencies).toContainText("Reference");
  await expect(currencies).toContainText("suggested");
  await expect(page.getByRole("row", { name: /topups/ })).toContainText("1 probe");
  await expect(page.getByRole("row", { name: /topups/ })).toContainText("drift");
  await expect(page.getByRole("row", { name: /ledger_entries/ })).toContainText("none");

  await page.getByRole("button", { name: /topups/ }).click();
  const topups = page.getByRole("region", { name: "Table topups" });
  await expect(topups.getByRole("link", { name: "topup_completed" })).toBeVisible();
  await expect(topups).toContainText("UAT: created_at is marked indexed, but no index starts with it");

  await page.getByRole("button", { name: /outbox_events/ }).click();
  await expect(page.getByRole("region", { name: "Table outbox_events" })).toContainText("Matches schema in all 2 environments");

  await page.getByRole("button", { name: /currencies/ }).click();
  const detail = page.getByRole("region", { name: "Table currencies" });
  await expect(detail.getByLabel("Label")).toHaveValue("reference");
  await expect(detail.getByLabel("Owner")).toHaveValue("payments-team");
  await expect(detail).toContainText("about 42 rows and no time column");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});

test("confirming wallets as Master shows the diff, writes it and drops the banner count", async ({ page }) => {
  await openTables(page);
  await page.getByRole("button", { name: /wallets/ }).click();
  const detail = page.getByRole("region", { name: "Table wallets" });
  await expect(detail.getByLabel("Label")).toHaveValue("master");
  await expect(detail.getByRole("button", { name: "Confirm" })).toBeDisabled();

  await detail.getByRole("button", { name: "Preview" }).click();
  await expect(detail).toContainText("wallet/catalog.yaml");
  await expect(detail.getByText("+    table: wallets")).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);

  await detail.getByLabel("Description").fill("One row per wallet.");
  await expect(detail).toContainText("Edited after this preview");
  await expect(detail.getByRole("button", { name: "Confirm" })).toBeDisabled();
  await detail.getByRole("button", { name: "Preview" }).click();
  await expect(detail.getByText("+    description: One row per wallet.")).toBeVisible();

  await detail.getByRole("button", { name: "Confirm" }).click();
  await expect(detail).toContainText("Written to wallet/catalog.yaml. Review and commit it with git.");
  await expect(page.getByRole("status", { name: "Needs review" })).toContainText("3 suggested labels not yet confirmed");
  await expect(page.getByRole("row", { name: /wallets/ })).not.toContainText("suggested");
});

test("a conflict offers Preview again, an invalid entry shows why", async ({ page }) => {
  await openTables(page);
  await page.getByRole("button", { name: /user_provider_ids/ }).click();
  const detail = page.getByRole("region", { name: "Table user_provider_ids" });
  await detail.getByRole("button", { name: "Preview" }).click();
  await detail.getByRole("button", { name: "Confirm" }).click();
  await expect(detail.getByRole("alert")).toContainText("changed since its preview; preview the change again");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await detail.getByRole("button", { name: "Preview again" }).click();
  await detail.getByRole("button", { name: "Confirm" }).click();
  await expect(detail).toContainText("Written to wallet/catalog.yaml");

  await page.getByRole("button", { name: /currencies/ }).click();
  const currencies = page.getByRole("region", { name: "Table currencies" });
  await currencies.getByLabel("Label").selectOption("transaction");
  await currencies.getByRole("button", { name: "Preview" }).click();
  await expect(currencies.getByRole("alert")).toContainText("a transaction table needs a time column");
});
