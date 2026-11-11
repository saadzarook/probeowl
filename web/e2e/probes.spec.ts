// Author: @SaadZarook
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const TIME_FILTER = "topups is a Transaction table: filter on its time column created_at (for example created_at >= :since), or look rows up by a key (a primary key, link or external key column = :parameter)";

async function openProbe(page: Page) {
  await page.goto("/probes");
  await expect(page.getByText("Pick a probe or start a new one")).toBeVisible();
  await page.getByLabel("Open probe").selectOption("topup_completed");
  await expect(page.getByRole("heading", { name: "topup_completed" })).toBeVisible();
}

test("the editor suggests the entity's locations as an expectation is typed", async ({ page }) => {
  await openProbe(page);
  await page.getByLabel("Probe YAML").click();
  await page.keyboard.press("Control+End");
  await page.keyboard.type("# count(led");
  const suggestions = page.getByRole("listbox");
  const ledger = suggestions.getByRole("option", { name: "ledger" });
  await expect(ledger).toHaveAttribute("aria-selected", "true");
  await expect(suggestions.getByRole("option", { name: "record" })).toHaveCount(0);
  // CodeMirror takes Enter once the list has been open a moment, so a stray Enter is never swallowed.
  await page.waitForTimeout(200);
  await page.keyboard.press("Enter");
  await expect(page.getByLabel("Probe YAML")).toContainText("# count(ledger");
});

test("owl reload in a terminal reaches an open editor: it reads again and says so, and the draft stays", async ({ page }) => {
  await openProbe(page);
  const reloadRequests: string[] = [];
  page.on("request", (r) => {
    if (r.url().includes("/api/applications/wallet/entities/topup")) reloadRequests.push(r.url());
  });
  // As if owl reload ran elsewhere: owl serve now answers with its next configuration.
  await page.evaluate(() => localStorage.setItem("mock-configuration", "1"));
  await page.getByLabel("Probe YAML").click();
  await page.keyboard.press("Control+End");
  await page.keyboard.type("# kept");
  const banner = page.getByRole("status").filter({ hasText: "owl serve reloaded its configuration" });
  await expect(banner).toBeVisible();
  await expect.poll(() => reloadRequests.length).toBeGreaterThan(0); // the entity is read again
  await expect(page.getByLabel("Probe YAML")).toContainText("# kept");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await banner.getByRole("button", { name: "Dismiss" }).click();
  await expect(banner).toHaveCount(0);
  await page.evaluate(() => localStorage.removeItem("mock-configuration"));
});

test("the Test run and Inspect ID fields name this entity's root key, never another entity's example", async ({ page }) => {
  await page.goto("/probes");
  await page.getByRole("button", { name: "Application" }).click();
  await page.getByRole("button", { name: /Card access/ }).last().click();
  await page.getByLabel("Open probe").selectOption("visitor_cards");
  // The mocks fail this file's first read, to show the retry.
  await page.getByRole("button", { name: "Retry" }).click();
  const box = page.getByRole("form", { name: "Test run" });
  await expect(box.getByLabel("ID", { exact: true })).toHaveAttribute("placeholder", "a visitor_profile_id or another identifier");
  // Inspect opens on Wallet: its entity is the top-up, so the field names topup_id.
  await page.goto("/inspect");
  await expect(page.getByLabel("Identifier")).toHaveAttribute("placeholder", "a topup_id or another identifier");
});

test("the YAML editor undoes and redoes, by keys and buttons, through a Check and a tab switch, and reverts to saved", async ({ page }) => {
  await openProbe(page);
  const yaml = page.getByLabel("Probe YAML");
  const history = page.getByRole("toolbar", { name: "Edit history" });
  await expect(history.getByRole("button", { name: "Undo" })).toBeDisabled();
  await expect(history.getByRole("button", { name: "Revert to saved" })).toBeDisabled();
  await yaml.click();
  await page.keyboard.press("Control+End");
  await page.keyboard.type("# first");
  await expect(yaml).toContainText("# first");
  // A Check, then another tab and back: the history is still there.
  await page.getByRole("region", { name: "Save checks" }).getByRole("button", { name: "Check" }).click();
  await page.getByRole("tab", { name: "Key graph" }).click();
  await page.getByRole("tab", { name: "YAML", exact: true }).click();
  await yaml.click();
  await page.keyboard.press("Control+z");
  await expect(yaml).not.toContainText("# first");
  await page.keyboard.press("Control+y");
  await expect(yaml).toContainText("# first");
  await history.getByRole("button", { name: "Undo" }).click();
  await expect(yaml).not.toContainText("# first");
  await history.getByRole("button", { name: "Redo" }).click();
  await expect(yaml).toContainText("# first");
  // Revert to saved puts the file back, as one step that can be undone.
  await history.getByRole("button", { name: "Revert to saved" }).click();
  await expect(yaml).not.toContainText("# first");
  await expect(yaml).toContainText("A comment only the real file has.");
  await expect(history.getByRole("button", { name: "Revert to saved" })).toBeDisabled();
  await history.getByRole("button", { name: "Undo" }).click();
  await expect(yaml).toContainText("# first");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});

test("a draft that passes shows every check Pass", async ({ page }) => {
  await openProbe(page);
  const panel = page.getByRole("region", { name: "Save checks" });
  await expect(panel.getByText("6 passed · 0 warnings · 0 failed")).toBeVisible();
  await expect(panel.getByText("Pass", { exact: true })).toHaveCount(6);
  await expect(page.getByText("Uses entity topup · 4 locations")).toBeVisible();
  const axe = await new AxeBuilder({ page }).analyze();
  expect(axe.violations).toEqual([]);
});

test("editing the YAML reruns the checks; an unreadable draft fails the Probe check", async ({ page }) => {
  await openProbe(page);
  const panel = page.getByRole("region", { name: "Save checks" });
  await page.getByLabel("Owner").fill("wallet-team");
  await expect(page.getByLabel("Probe YAML")).toContainText("owner: wallet-team");
  await page.getByLabel("Probe YAML").fill("probe: [");
  await expect(panel.getByText(/the YAML cannot be read/)).toBeVisible();
  await expect(panel.getByText("Fix the failed checks to save")).toBeVisible();
});

test("a time filter missing in the entity fails the check and a test run is refused", async ({ page }) => {
  await openProbe(page);
  await page.getByRole("tab", { name: "Entity YAML" }).click();
  await page.getByLabel("Entity YAML").fill(
    "entity: topup\nroot_key: topup_id\nlocations: {record: {using: wallet_db, lookup: 'SELECT id FROM topups WHERE status = :status'}}",
  );
  const panel = page.getByRole("region", { name: "Save checks" });
  await expect(panel.getByText(TIME_FILTER)).toBeVisible();
  await expect(panel.getByText("Fix the failed checks to save")).toBeVisible();

  await page.getByRole("main").getByLabel("ID", { exact: true }).fill("tp_91f2c7");
  await page.getByLabel("Reason").selectOption("incident");
  await page.getByLabel(/^Reference/).fill("WALLET-482");
  await page.getByRole("button", { name: "Run draft" }).click();
  await expect(page.getByRole("alert")).toContainText("Time filter: record");
  await expect(page.getByRole("alert")).toContainText("See the Save checks panel");
});

test("Run draft on tp_91f2c7 follows the run to Failed under a draft version", async ({ page }) => {
  await openProbe(page);
  await page.getByRole("main").getByLabel("ID", { exact: true }).fill("tp_91f2c7");
  await page.getByLabel("Reason").selectOption("incident");
  await page.getByLabel(/^Reference/).fill("WALLET-482");
  await page.getByRole("button", { name: "Run draft" }).click();
  const box = page.getByRole("form", { name: "Test run" });
  await expect(box.getByText("Failed", { exact: true })).toBeVisible();
  await expect(box.getByText(/of \d+ expectations/)).toBeVisible();
  await expect(box.getByText(/^draft:sha256:/)).toBeVisible();
  await expect(box.getByRole("link", { name: "Open run" })).toBeVisible();
  // The link opens the run on the Runs screen, not a placeholder.
  await box.getByRole("link", { name: "Open run" }).click();
  await expect(page).toHaveURL(/\/runs\?run=/);
  await expect(page.getByRole("region", { name: "Run result" })).toBeVisible();
  await expect(page.getByRole("link", { name: "← All runs" })).toBeVisible();
});

test("the Key graph tab lists locations and their key_from", async ({ page }) => {
  await openProbe(page);
  await page.getByRole("tab", { name: "Key graph" }).click();
  await expect(page.getByRole("list", { name: "Key graph" })).toContainText("record.ledger_txn_id");
});

/** A draft of a new probe called name. */
async function newProbeNamed(page: Page, name: string) {
  await page.goto("/probes");
  await page.getByRole("button", { name: "New probe" }).click();
  const form = page.getByRole("form", { name: "New probe" });
  await form.getByLabel("Probe id").fill(name);
  await form.getByLabel("Title").fill(`Draft ${name}`);
  await form.getByRole("button", { name: "Start the draft" }).click();
  await expect(page.getByLabel("Probe YAML")).toContainText(`title: Draft ${name}`);
  await expect(page.getByRole("button", { name: "Save", exact: true })).toBeEnabled();
}

test("an existing probe opens its real file, and saves once it has changed", async ({ page }) => {
  await openProbe(page);
  const yaml = page.getByLabel("Probe YAML");
  await expect(yaml).toContainText("A comment only the real file has.");
  const save = page.getByRole("button", { name: "Save", exact: true });
  await expect(save).toBeDisabled();
  await expect(page.getByText("Nothing to save")).toBeVisible();

  await page.getByLabel("Environment").first().selectOption("UAT");
  await page.getByLabel("Owner").fill("wallet-team");
  await expect(save).toBeEnabled();
  await save.click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByLabel("Diff of wallet/probes/topup_completed.yaml")).toContainText("owner: wallet-team");
  await dialog.getByRole("button", { name: "Confirm" }).click();
  await expect(dialog.getByRole("status")).toContainText("Saved wallet/probes/topup_completed.yaml");
  await dialog.getByRole("button", { name: "Close" }).click();
  // What was saved is now what is open.
  await expect(page.getByText("Nothing to save")).toBeVisible();
});

test("the left panel changes only its own field's text", async ({ page }) => {
  await openProbe(page);
  const yaml = page.getByLabel("Probe YAML");
  const before = await yaml.innerText();
  const owner = /owner: .*/.exec(before)?.[0] ?? "";
  await page.getByLabel("Owner").fill("wallet-team");
  await expect(yaml).toContainText("owner: wallet-team");
  expect(await yaml.innerText()).toBe(before.replace(owner, "owner: wallet-team"));
});

test("an environment toggle adds one line and removes it cleanly", async ({ page }) => {
  await openProbe(page);
  const yaml = page.getByLabel("Probe YAML");
  const before = await yaml.innerText();
  await page.getByRole("checkbox", { name: "UAT" }).uncheck();
  await expect(yaml).toContainText("environments: [prod]");
  expect((await yaml.innerText()).startsWith(before.trimEnd())).toBe(true);
});

test("an edited entity keeps its comment and is saved with the probe", async ({ page }) => {
  await openProbe(page);
  await page.getByLabel("Environment").first().selectOption("UAT");
  await page.getByRole("tab", { name: "Entity YAML" }).click();
  const entity = page.getByLabel("Entity YAML");
  await expect(entity).toContainText("A comment only the real file has.");
  await entity.fill(`${await entity.innerText()}\n# edited\n`);
  await page.getByRole("button", { name: "Save", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("Both files are saved together, or neither.")).toBeVisible();
});

test("a new probe is named before its draft starts: never new_probe, never an existing probe's id", async ({ page }) => {
  await page.goto("/probes");
  await page.getByRole("button", { name: "New probe" }).click();
  const form = page.getByRole("form", { name: "New probe" });
  const start = form.getByRole("button", { name: "Start the draft" });
  await expect(start).toBeDisabled();
  await form.getByLabel("Probe id").fill("Topup Check");
  await expect(form.getByRole("alert")).toContainText("use a-z, 0-9 and _");
  await form.getByLabel("Probe id").fill("topup_completed");
  await expect(form.getByRole("alert")).toContainText("a probe named topup_completed already exists");
  await expect(start).toBeDisabled();
  await form.getByLabel("Probe id").fill("topup_ledger_posted");
  await expect(start).toBeDisabled(); // a title too
  await form.getByLabel("Title").fill("Top-up posted to the ledger");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await start.click();
  const yaml = page.getByLabel("Probe YAML");
  await expect(yaml).toContainText("probe: topup_ledger_posted");
  await expect(yaml).toContainText("title: Top-up posted to the ledger");
  await expect(page.getByRole("region", { name: "Probe settings" }).getByLabel("Title")).toHaveValue("Top-up posted to the ledger");

  // A title emptied in a new draft blocks saving.
  await page.getByRole("region", { name: "Probe settings" }).getByLabel("Title").fill("");
  await expect(page.getByText("Give the probe a title to save it")).toBeVisible();
  await expect(page.getByRole("button", { name: "Save", exact: true })).toBeDisabled();
});

test("a file that cannot be opened says so and retries, and leaves the editor empty", async ({ page }) => {
  await page.goto("/probes");
  await page.getByRole("button", { name: "Application" }).click();
  await page.getByRole("button", { name: /Card access/ }).last().click();
  await page.getByLabel("Open probe").selectOption("visitor_cards");
  await expect(page.getByRole("alert")).toContainText("The file could not be read.");
  await expect(page.getByLabel("Probe YAML")).toHaveCount(0);
  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByLabel("Probe YAML")).toContainText("A comment only the real file has.");
});

test("Save previews the probe and entity diffs, then confirms both together", async ({ page }) => {
  await newProbeNamed(page, "topup_new");
  await page.getByLabel("Environment").first().selectOption("UAT");
  await page.getByRole("tab", { name: "Entity YAML" }).click();
  await page.getByLabel("Entity YAML").fill("entity: topup\nroot_key: topup_id\nlocations: {record: {using: wallet_db, lookup: 'SELECT id FROM topups WHERE id = :topup_id'}}\n");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("Both files are saved together, or neither.")).toBeVisible();
  await expect(dialog.getByLabel("Diff of wallet/probes/topup_new.yaml")).toBeVisible();
  await expect(dialog.getByLabel("Diff of wallet/entities/topup_new.yaml")).toBeVisible();
  await dialog.getByRole("button", { name: "Confirm" }).click();
  await expect(dialog.getByRole("status")).toContainText("Saved wallet/probes/topup_new.yaml");
  await expect(dialog.getByRole("status")).toContainText("Commit it when you're ready");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});

test("Save in Production lists the plan checks that could not run and needs the tick to confirm", async ({ page }) => {
  await newProbeNamed(page, "topup_new");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  const dialog = page.getByRole("dialog");
  // The preview refuses nothing: it shows the diff and asks.
  await expect(dialog.getByLabel("Diff of wallet/probes/topup_new.yaml")).toBeVisible();
  await expect(dialog.getByRole("list", { name: "Plan checks that could not run" })).toContainText("ledger_db has no binding in prod");
  await expect(dialog.getByText("This is recorded in the audit with your name.")).toBeVisible();
  const confirm = dialog.getByRole("button", { name: "Confirm" });
  await expect(confirm).toBeDisabled();
  const sent: { save_without_plan_check?: boolean }[] = [];
  page.on("request", (r) => {
    if (r.method() === "PUT") sent.push(r.postDataJSON() as { save_without_plan_check?: boolean });
  });
  await dialog.getByRole("checkbox", { name: "Save without plan check" }).check();
  await confirm.click();
  await expect(dialog.getByRole("status")).toContainText("Saved");
  expect(sent.at(-1)?.save_without_plan_check).toBe(true);
});

test("outside Production a plan check that could not run asks nothing", async ({ page }) => {
  await newProbeNamed(page, "topup_new");
  await page.getByLabel("Environment").first().selectOption("UAT");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("button", { name: "Confirm" })).toBeEnabled();
  await expect(dialog.getByRole("checkbox")).toHaveCount(0);
});

test("Save says a file changed, and the new preview needs a new tick", async ({ page }) => {
  await newProbeNamed(page, "topup_conflict");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("checkbox", { name: "Save without plan check" }).check();
  await dialog.getByRole("button", { name: "Confirm" }).click();
  await expect(dialog.getByRole("alert")).toContainText("A file changed since the preview");
  await dialog.getByRole("button", { name: "Preview again" }).click();
  await expect(dialog.getByRole("checkbox", { name: "Save without plan check" })).not.toBeChecked();
  await expect(dialog.getByRole("button", { name: "Confirm" })).toBeDisabled();
  await dialog.getByRole("checkbox", { name: "Save without plan check" }).check();
  await dialog.getByRole("button", { name: "Confirm" }).click();
  await expect(dialog.getByRole("status")).toContainText("Saved");
});

test("a confirm with nothing to write says so", async ({ page }) => {
  await newProbeNamed(page, "topup_unchanged");
  await page.getByLabel("Environment").first().selectOption("UAT");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "Confirm" }).click();
  await expect(dialog.getByRole("status")).toContainText("Nothing changed");
});

test("a save that would break the folder names the file", async ({ page }) => {
  await newProbeNamed(page, "topup_broken");
  await page.getByLabel("Environment").first().selectOption("UAT");
  await page.getByRole("button", { name: "Save", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "Confirm" }).click();
  await expect(dialog.getByRole("alert")).toContainText("saving would break the folder: wallet/connectors/broken.yaml");
});
