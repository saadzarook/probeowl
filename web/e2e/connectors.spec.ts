// Author: @SaadZarook
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

async function openPayouts(page: Page) {
  await page.goto("/connectors");
  await page.getByRole("button", { name: "Application" }).click();
  await page.getByRole("button", { name: /Payouts/ }).last().click();
  await expect(page.getByRole("row", { name: /payout_db/ })).toBeVisible();
}

test("lists the wallet connectors, REST never checked, with no hosts or logins", async ({ page }) => {
  await page.goto("/connectors");
  await expect(page.getByRole("heading", { name: "Connectors" })).toBeVisible();
  await expect(page.getByRole("row", { name: /wallet_db/ })).toContainText("SQL Server");
  await expect(page.getByRole("row", { name: /ledger_db/ })).toContainText("Oracle");
  await expect(page.getByRole("row", { name: /checkout_api/ })).toContainText("Not checked");
  const text = await page.locator("main").innerText();
  expect(text).not.toContain("127.0.0.1");
  expect(text).not.toContain("probeowl_ro");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});

test("every health value shows its text and reason", async ({ page }) => {
  await openPayouts(page);
  for (const [connector, health, reason] of [
    ["ledger_replica", "Healthy", "None, nothing to report."],
    ["settlement_replica", "Lagging", "240 s behind"],
    ["reporting_replica", "Paused", "paused until the replica catches up"],
    ["payout_db", "Unreachable", "the connection was refused or could not be made; check the database is up and the binding is right"],
    ["bank_api", "Not checked", "never called"],
    ["notify_db", "Not configured", "no binding"],
  ] as const) {
    await page.getByRole("button", { name: connector }).click();
    await expect(page.getByRole("row", { name: new RegExp(connector) })).toContainText(health);
    await expect(page.getByRole("region", { name: `Connector ${connector}` })).toContainText(reason);
  }
  await page.getByRole("button", { name: "bank_files_db" }).click();
  const detail = page.getByRole("region", { name: "Connector bank_files_db" });
  await expect(detail).toContainText("Denied");
  await expect(detail).toContainText("GRANT SHOWPLAN");
  await expect(detail.getByRole("list", { name: "Guards" }).getByRole("listitem").first()).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});

test("a denied plan access warns in the row in Production only", async ({ page }) => {
  await openPayouts(page);
  await page.getByLabel("Environment").selectOption("uat");
  await expect(page.getByRole("row", { name: /bank_files_db/ })).toBeVisible();
  await expect(page.getByRole("row", { name: /bank_files_db/ }).getByRole("note")).toHaveCount(0);
  await page.getByLabel("Environment").selectOption("prod");
  await expect(page.getByRole("row", { name: /bank_files_db/ }).getByRole("note")).toContainText("GRANT SHOWPLAN");
});

test("changing the environment refetches", async ({ page }) => {
  const seen: string[] = [];
  page.on("request", (r) => {
    const environment = new URL(r.url()).searchParams.get("environment");
    if (r.url().includes("/connectors") && environment) seen.push(environment);
  });
  await page.goto("/connectors");
  await expect(page.getByRole("row", { name: /wallet_db/ })).toBeVisible();
  await page.getByLabel("Environment").selectOption("uat");
  await expect.poll(() => seen).toContain("uat");
  await page.getByLabel("Environment").selectOption("prod");
  await expect.poll(() => seen).toContain("prod");
});

test("an application without connectors shows the empty state", async ({ page }) => {
  await page.goto("/connectors");
  await page.getByRole("button", { name: "Application" }).click();
  await page.getByRole("button", { name: /Orders/ }).last().click();
  await expect(page.getByRole("heading", { name: "No connectors in Orders" })).toBeVisible();
  await expect(page.getByText("orders/connectors/")).toBeVisible();
  await expect(page.getByText("owl connector add --application orders", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Copy command" })).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await expect(page.getByRole("link", { name: "Adding a connector" })).toHaveAttribute("href", /\/connectors\/#adding-a-connector$/);
});

test("a 404 shows the problem and a retry", async ({ page }) => {
  await page.goto("/connectors");
  await page.getByRole("button", { name: "Application" }).click();
  await page.getByRole("button", { name: /Onboarding/ }).last().click();
  await page.getByLabel("Environment").selectOption("dev");
  await expect(page.getByRole("alert")).toContainText("There is no application and environment");
  await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});

test("the environment buttons switch the bindings, and say what is showing", async ({ page }) => {
  await page.goto("/connectors");
  await expect(page.getByText("Showing Production bindings · guards locked")).toBeVisible();
  const bindings = page.getByRole("group", { name: "Show bindings for" });
  await bindings.getByRole("button", { name: "UAT" }).click();
  await expect(page.getByText("Showing UAT bindings", { exact: true })).toBeVisible();
  await expect(bindings.getByRole("button", { name: "UAT" })).toHaveAttribute("aria-pressed", "true");
  // One choice across the app: the top bar follows.
  await expect(page.getByLabel("Environment")).toHaveValue("uat");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});

test("a binding that needs attention is marked, with its reason at the top of the detail", async ({ page }) => {
  await openPayouts(page);
  await expect(page.getByRole("row", { name: /settlement_replica/ })).toHaveAttribute("data-attention", "true");
  await expect(page.getByRole("row", { name: /ledger_replica/ })).toHaveAttribute("data-attention", "false");
  await page.getByRole("button", { name: "settlement_replica" }).click();
  const detail = page.getByRole("region", { name: "Connector settlement_replica" });
  await expect(detail.locator(".connector-reason")).toContainText("240 s behind");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});

test("a connector that is not a replica shows n/a for its lag", async ({ page }) => {
  await page.goto("/connectors");
  await expect(page.getByRole("row", { name: /checkout_api/ })).toContainText("n/a");
});

async function stopWalletDb(page: Page, reason = "replica on fire") {
  await page.goto("/connectors");
  await page.getByRole("button", { name: "wallet_db" }).click();
  await page.getByRole("checkbox", { name: "Kill switch" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Reason").fill(reason);
  await dialog.getByRole("button", { name: "Stop reads" }).click();
  await expect(dialog).toHaveCount(0);
}

test("a connector's kill switch needs a reason, then shows Stopped and a banner on every screen", async ({ page }) => {
  await page.goto("/connectors");
  await page.getByRole("button", { name: "wallet_db" }).click();
  await page.getByRole("checkbox", { name: "Kill switch" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("button", { name: "Stop reads" })).toBeDisabled();
  await expect(dialog).toContainText("connector wallet_db of wallet, in every environment");
  await expect(dialog).toContainText("Recorded in the audit with your name.");
  await dialog.getByLabel("Reason").fill("replica on fire");
  await dialog.getByRole("button", { name: "Stop reads" }).click();

  await expect(page.getByRole("row", { name: /wallet_db/ })).toContainText("Stopped");
  await expect(page.getByRole("row", { name: /wallet_db/ })).toHaveAttribute("data-attention", "true");
  await expect(page.getByRole("checkbox", { name: "Kill switch" })).toBeChecked();
  const banner = page.getByRole("alert", { name: "Kill switches on" });
  await expect(banner).toContainText("connector wallet_db of wallet, in every environment: replica on fire · by s.khan");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);

  // Client-side, so the state survives: the banner stays on the other screens.
  await page.getByRole("link", { name: "Inspect" }).click();
  await expect(page.getByRole("alert", { name: "Kill switches on" })).toBeVisible();
});

test("turning it off needs a reason too, and clears the banner", async ({ page }) => {
  await stopWalletDb(page);
  await page.getByRole("checkbox", { name: "Kill switch" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("button", { name: "Resume reads" })).toBeDisabled();
  await dialog.getByLabel("Reason").fill("fixed");
  await dialog.getByRole("button", { name: "Resume reads" }).click();
  await expect(page.getByRole("alert", { name: "Kill switches on" })).toHaveCount(0);
  await expect(page.getByRole("row", { name: /wallet_db/ })).not.toContainText("Stopped");
  await expect(page.getByRole("checkbox", { name: "Kill switch" })).not.toBeChecked();
});

test("cancelling changes nothing", async ({ page }) => {
  await page.goto("/connectors");
  await page.getByRole("button", { name: "wallet_db" }).click();
  await page.getByRole("checkbox", { name: "Kill switch" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Cancel" }).click();
  await expect(page.getByRole("checkbox", { name: "Kill switch" })).not.toBeChecked();
  await expect(page.getByRole("alert", { name: "Kill switches on" })).toHaveCount(0);
});

test("an application's and the global switch stop every connector they cover", async ({ page }) => {
  await page.goto("/connectors");
  await page.getByRole("button", { name: "Stop Wallet" }).click();
  await page.getByRole("dialog").getByLabel("Reason").fill("wallet incident");
  await page.getByRole("dialog").getByRole("button", { name: "Stop reads" }).click();
  for (const connector of ["wallet_db", "ledger_db", "checkout_api"]) {
    await expect(page.getByRole("row", { name: new RegExp(connector) })).toContainText("Stopped");
  }
  await expect(page.getByRole("button", { name: "Resume Wallet" })).toBeVisible();

  await page.getByRole("button", { name: "wallet_db" }).click();
  await expect(page.getByRole("region", { name: "Connector wallet_db" })).toContainText("turning this one off will not resume it");

  await page.getByRole("button", { name: "Stop everything" }).click();
  await expect(page.getByRole("dialog")).toContainText("every connector, in every application");
  await page.getByRole("dialog").getByLabel("Reason").fill("all stop");
  await page.getByRole("dialog").getByRole("button", { name: "Stop reads" }).click();
  await expect(page.getByRole("button", { name: "Resume everything" })).toBeVisible();
  await expect(page.getByRole("alert", { name: "Kill switches on" }).getByRole("listitem")).toHaveCount(2);
});

test("a store that cannot be read says runs are refused, and the switches cannot be changed", async ({ page }) => {
  await page.goto("/connectors");
  await page.getByRole("button", { name: "Application" }).click();
  await page.getByRole("button", { name: /Orders/ }).last().click();
  await page.getByRole("button", { name: "Stop Orders" }).waitFor();
  await expect(page.getByRole("alert").filter({ hasText: "Kill switch unknown." })).toContainText("Runs are refused", { timeout: 20_000 });
  await expect(page.getByRole("button", { name: "Stop Orders" })).toBeDisabled();
});

test("each row says the binding's role and limits", async ({ page }) => {
  await page.goto("/connectors");
  const wallet = page.getByRole("row", { name: /wallet_db/ });
  await expect(wallet).toContainText("Readable secondary");
  await expect(wallet).toContainText("2 s · 100 rows");
  await expect(page.getByRole("row", { name: /ledger_db/ })).toContainText("Active Data Guard standby");
  const rest = page.getByRole("row", { name: /checkout_api/ });
  await expect(rest).toContainText("5 s · 100 req/min · GET only");
  await expect(rest.locator(".connector-role")).toHaveCount(0);
  for (const header of ["Connector", "Type", "Limits", "Lag", "Status"]) {
    await expect(page.getByRole("columnheader", { name: header })).toBeVisible();
  }
  await expect(page.getByText(/pool/)).toHaveCount(0);
});

test("an Oracle binding says its session time zone, and SQL Server has none to say", async ({ page }) => {
  await page.goto("/connectors");
  await page.getByRole("button", { name: "ledger_db" }).click();
  const ledger = page.getByRole("region", { name: "Connector ledger_db" });
  await expect(ledger.getByRole("definition").filter({ hasText: "the database's own (DBTIMEZONE)" })).toBeVisible();
  await page.getByRole("button", { name: "wallet_db" }).click();
  await expect(page.getByRole("region", { name: "Connector wallet_db" }).getByText("Session time zone")).toHaveCount(0);
});

test("the guards panel lists what is forbidden, and a REST API has no such row", async ({ page }) => {
  await page.goto("/connectors");
  await page.getByRole("button", { name: "wallet_db" }).click();
  const detail = page.getByRole("region", { name: "Connector wallet_db" });
  await expect(detail).toContainText("NOLOCK, HOLDLOCK, TABLOCKX, UPDLOCK, temp tables");
  await expect(detail).toContainText("SQL Server · Readable secondary · payments-team");
  await page.getByRole("button", { name: "checkout_api" }).click();
  const rest = page.getByRole("region", { name: "Connector checkout_api" });
  await expect(rest).toContainText("REST · payments-team");
  await expect(rest.getByText("Forbidden")).toHaveCount(0);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});

test("Test connection says it reached the binding", async ({ page }) => {
  await page.goto("/connectors");
  await page.getByRole("button", { name: "wallet_db" }).click();
  const detail = page.getByRole("region", { name: "Connector wallet_db" });
  await detail.getByRole("button", { name: "Test connection" }).click();
  await expect(detail.getByRole("status")).toHaveText("Reached in 42 ms.");
});

test("Test connection says why a binding could not be reached", async ({ page }) => {
  await openPayouts(page);
  await page.getByRole("button", { name: "payout_db" }).click();
  const detail = page.getByRole("region", { name: "Connector payout_db" });
  await detail.getByRole("button", { name: "Test connection" }).click();
  await expect(detail.getByRole("status")).toContainText("Not reached: the connection was refused");
});

test("Test connection cannot start on a stopped connector or one with no binding", async ({ page }) => {
  await stopWalletDb(page);
  const detail = page.getByRole("region", { name: "Connector wallet_db" });
  await expect(detail.getByRole("button", { name: "Test connection" })).toBeDisabled();
  await openPayouts(page);
  await page.getByRole("button", { name: "notify_db" }).click();
  await expect(page.getByRole("region", { name: "Connector notify_db" }).getByRole("button", { name: "Test connection" })).toBeDisabled();
});

test("a kill switch turned on since the screen loaded shows the refusal", async ({ page }) => {
  await openPayouts(page);
  await page.getByRole("button", { name: "ledger_replica" }).click();
  const detail = page.getByRole("region", { name: "Connector ledger_replica" });
  await detail.getByRole("button", { name: "Test connection" }).click();
  await expect(detail.getByRole("alert")).toContainText("stopped by the global kill switch: on fire");
});

test("every column fits beside the detail panel, Status included", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await openPayouts(page);
  const scroll = page.locator(".table-scroll");
  const overflow = await scroll.evaluate((element) => element.scrollWidth - element.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});

test("the guards show on a stopped or unreachable connector, and only no binding has none", async ({ page }) => {
  await stopWalletDb(page);
  const stopped = page.getByRole("region", { name: "Connector wallet_db" });
  await expect(stopped.getByRole("list", { name: "Guards" })).toContainText("row ceiling 1000");
  await expect(stopped).toContainText("up to 1,000");
  await openPayouts(page);
  await page.getByRole("button", { name: "payout_db" }).click();
  await expect(page.getByRole("region", { name: "Connector payout_db" }).getByRole("list", { name: "Guards" })).toContainText("statement timeout 2 s");
  await page.getByRole("button", { name: "notify_db" }).click();
  await expect(page.getByRole("region", { name: "Connector notify_db" })).toContainText("None, this connector has no binding here.");
});

test("a lag the login cannot read says which grant is missing, not a bare unknown", async ({ page }) => {
  await openPayouts(page);
  const row = page.getByRole("row", { name: /refunds_replica/ });
  await expect(row).toContainText("replica lag unknown: missing VIEW DATABASE PERFORMANCE STATE");
  await expect(row.getByText(/^Unknown$/)).toHaveCount(0);
});

test("Reload configuration says what it loaded, and that nothing changed", async ({ page }) => {
  await page.goto("/connectors");
  await page.getByRole("button", { name: "Reload configuration" }).click();
  const result = page.getByRole("status").filter({ hasText: "Reloaded:" });
  await expect(result).toContainText("Reloaded: 2 applications, 5 connectors, 2 probes.");
  await expect(result).toContainText("No application or connector changed.");
  await expect(result).toContainText("change only when owl serve is restarted");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});

test("Reload configuration names the connectors that changed", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("mock-reload", "changed"));
  await page.goto("/connectors");
  await page.getByRole("button", { name: "Reload configuration" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Reloaded:" })).toContainText("Connectors changed: wallet/ledger_db.");
});

test("a reload refused while a run is running says why and offers a retry", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("mock-reload", "running"));
  await page.goto("/connectors");
  await page.getByRole("button", { name: "Reload configuration" }).click();
  const alert = page.getByRole("alert").filter({ hasText: "reload when" });
  await expect(alert).toContainText("1 run is running; reload when they have finished");
  await expect(alert.getByRole("button", { name: "Retry" })).toBeVisible();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});

test("a folder owl serve would refuse is not reloaded, and the error says to run owl check", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("mock-reload", "broken"));
  await page.goto("/connectors");
  await page.getByRole("button", { name: "Reload configuration" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "owl check" })).toContainText("environments.uat.host: Field required");
  await expect(page.getByRole("row", { name: /wallet_db/ })).toBeVisible();
});
