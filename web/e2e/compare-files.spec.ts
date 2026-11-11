// Author: @SaadZarook
import AxeBuilder from "@axe-core/playwright";
import { readFile } from "node:fs/promises";
import { expect, test, type Download, type Page } from "@playwright/test";

const SECRET = "live-abc123";
const OTHER = "test-xyz789";
const LEFT = `payments:\n  api_key: ${SECRET}\n  max: 3\n`;
const RIGHT = `payments:\n  api_key: ${OTHER}\n  max: 5\n`;

async function paste(page: Page, title: string, text: string) {
  const card = page.getByRole("region", { name: title, exact: true });
  await card.getByRole("group", { name: `${title} input` }).getByRole("button", { name: "Paste" }).click();
  await card.getByLabel("Text").fill(text);
}

async function compareTwo(page: Page, left = LEFT, right = RIGHT) {
  await page.goto("/tools/compare-files");
  await paste(page, "File A", left);
  await paste(page, "File B", right);
  await page.getByRole("button", { name: "Compare", exact: true }).click();
}

const differences = (page: Page) => page.getByRole("region", { name: "Differences" });

async function textOf(download: Download): Promise<string> {
  const path = await download.path();
  return readFile(path, "utf-8");
}

test("Tools lists the plugin, and its screen matches the design's words", async ({ page }) => {
  await page.goto("/inspect");
  await page.getByRole("navigation", { name: "Tools" }).getByRole("link", { name: "Compare files" }).click();
  await expect(page).toHaveURL(/\/tools\/compare-files$/);
  await expect(page.getByRole("heading", { name: "Compare files" })).toBeVisible();
  await expect(page.getByText("Plugin · first-party · runs on this machine; files are never stored")).toBeVisible();
  await expect(page.getByText("The audit records that a comparison ran, with file names, sizes and hashes only. Up to 10 MB per file.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Compare", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Export CSV" })).toBeDisabled();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});

test("a disabled plugin has no Tools entry and no screen", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("mock-plugins-off", "1"));
  await page.goto("/tools/compare-files");
  await expect(page).toHaveURL(/\/inspect$/);
  await expect(page.getByRole("navigation", { name: "Tools" })).toHaveCount(0);
});

test("the differences show counts, paths and masked values, with no secret on screen", async ({ page }) => {
  await compareTwo(page);
  const found = differences(page);
  await expect(found.getByText("2 changed", { exact: true })).toBeVisible();
  await expect(found.getByText("38 unchanged", { exact: true })).toBeVisible();
  await expect(found.getByText("1 masked · 1 ignored")).toBeVisible();
  await expect(found.getByRole("row", { name: /payments\.max/ })).toContainText("3");
  await expect(found.getByRole("row", { name: /payments\.max/ })).toContainText("5");
  await expect(found).not.toContainText(SECRET);
  await expect(found).not.toContainText(OTHER);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
});

test("Reveal reads the person's own file, sends nothing, and hides again", async ({ page }) => {
  await compareTwo(page);
  const row = differences(page).getByRole("row", { name: /payments\.api_key/ });
  await expect(row).not.toContainText(SECRET);
  const sent: string[] = [];
  page.on("request", (r) => sent.push(r.url()));
  await row.getByRole("button", { name: "Reveal payments.api_key" }).click();
  await expect(row).toContainText(SECRET);
  await expect(row).toContainText(OTHER);
  expect(sent).toEqual([]);
  await row.getByRole("button", { name: "Hide payments.api_key" }).click();
  await expect(row).not.toContainText(SECRET);
});

test("a value that cannot be found exactly has no Reveal", async ({ page }) => {
  await compareTwo(page, "no-reveal\n", RIGHT);
  const row = differences(page).getByRole("row", { name: /payments\.api_key/ });
  await expect(row).toContainText("Changed");
  await expect(row.getByRole("button", { name: /Reveal/ })).toHaveCount(0);
});

test("Reveal reads what was compared, not what was edited afterwards", async ({ page }) => {
  await compareTwo(page);
  await page.getByRole("region", { name: "File A", exact: true }).getByLabel("Text").fill("edited since");
  const row = differences(page).getByRole("row", { name: /payments\.api_key/ });
  await row.getByRole("button", { name: "Reveal payments.api_key" }).click();
  await expect(row).toContainText(SECRET);
});

test("the side-by-side view hides secrets and jumps to a selected path", async ({ page }) => {
  await compareTwo(page);
  const side = page.getByRole("region", { name: "Side by side" });
  await expect(side).toContainText("max: 3");
  await expect(side).toContainText("••••••••");
  await expect(side).not.toContainText(SECRET);
  await expect(side).not.toContainText(OTHER);
  await differences(page).getByRole("button", { name: "payments.max" }).click();
  await expect(side.getByRole("heading")).toContainText("payments.max");
  await expect(side.locator('[data-target="true"]').first()).toContainText("max: 3");
});

test("the side-by-side view is withheld when a secret cannot be located", async ({ page }) => {
  await compareTwo(page, "withheld\n", RIGHT);
  const side = page.getByRole("region", { name: "Side by side" });
  await expect(side).toContainText("Hidden: a masked value could not be located");
  await expect(side.locator("pre")).toHaveCount(0);
});

test("a fallback note sits next to the rows it affects", async ({ page }) => {
  await page.goto("/tools/compare-files");
  await paste(page, "File A", "fallback\n");
  await paste(page, "File B", "fallback\n");
  await page.getByLabel("Arrays").selectOption("by_key");
  await page.getByLabel("Key field or CSV column").fill("id");
  await page.getByRole("button", { name: "Compare", exact: true }).click();
  const rows = differences(page).getByRole("row");
  const note = rows.filter({ hasText: "items: matched by position" });
  await expect(note).toBeVisible();
  const texts = await rows.allInnerTexts();
  const at = texts.findIndex((t) => t.includes("matched by position"));
  expect(texts[at + 1]).toContain("items[0].v");
});

test("a note not about arrays stays above the table", async ({ page }) => {
  await compareTwo(page, "looks different\n", RIGHT);
  const found = differences(page);
  await expect(found.getByRole("note").first()).toContainText("different formats");
  await expect(found.locator(".compare-note")).toHaveCount(0);
});

test("the tolerance option explains its limit", async ({ page }) => {
  await page.goto("/tools/compare-files");
  await expect(page.getByLabel("Number tolerance")).toHaveAccessibleDescription(/only pair if they are in the same order among those left/);
});

test("Match by key needs its key before Compare", async ({ page }) => {
  await page.goto("/tools/compare-files");
  await paste(page, "File A", LEFT);
  await paste(page, "File B", RIGHT);
  await page.getByLabel("Arrays").selectOption("by_key");
  await expect(page.getByRole("button", { name: "Compare", exact: true })).toBeDisabled();
  await page.getByLabel("Key field or CSV column").fill("id");
  await expect(page.getByRole("button", { name: "Compare", exact: true })).toBeEnabled();
});

test("the options reach the request", async ({ page }) => {
  await page.goto("/tools/compare-files");
  await paste(page, "File A", LEFT);
  await paste(page, "File B", RIGHT);
  await page.getByLabel("Format").selectOption("yaml");
  await page.getByLabel("Ignore paths").fill("*.updated_at, build.number");
  await page.getByLabel("Number tolerance").fill("0.5");
  await page.getByLabel("Ignore case (values, not keys)").check();
  const request = page.waitForRequest((r) => r.url().includes("/compare") && r.method() === "POST");
  await page.getByRole("button", { name: "Compare", exact: true }).click();
  const body = (await request).postDataJSON() as Record<string, never>;
  expect(body["format"]).toBe("yaml");
  expect(body["options"]).toMatchObject({ ignore_paths: ["*.updated_at", "build.number"], number_tolerance: "0.5", ignore_case: true, ignore_key_order: true });
});

test("exports hold the masked result, never a secret, even with a value revealed", async ({ page }) => {
  await compareTwo(page);
  await differences(page).getByRole("button", { name: "Reveal payments.api_key" }).click();
  await page.getByRole("region", { name: "File A", exact: true }).getByLabel("Label").fill("prod");

  const markdown = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export Markdown" }).click();
  const md = await textOf(await markdown);
  expect(md).toContain("| payments.api_key | [masked] | [masked] | changed |");
  expect(md).toContain("# Compare files: prod and File B");
  expect(md).not.toContain(SECRET);

  const sheet = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export CSV" }).click();
  const csv = await textOf(await sheet);
  expect(csv).toContain("path,prod,File B,change");
  expect(csv).toContain("payments.api_key,[masked],[masked],changed");
  expect(csv).not.toContain(SECRET);
  expect(csv).not.toContain(OTHER);
});

test("a CSV cell that a spreadsheet would run is made text", async ({ page }) => {
  await compareTwo(page, "formula\n", RIGHT);
  const sheet = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export CSV" }).click();
  const csv = await textOf(await sheet);
  expect(csv).toContain(`"'=HYPERLINK(""x"")"`);
  expect(csv).toContain("'-5");
  expect(csv).toContain("'@cmd");
  expect(csv).toContain("'+1");
});

test("an uploaded file is compared, and Replace clears nothing already compared", async ({ page }) => {
  await page.goto("/tools/compare-files");
  await page.getByLabel("File A file").setInputFiles({ name: "prod.yaml", mimeType: "text/yaml", buffer: Buffer.from(LEFT) });
  await page.getByLabel("File B file").setInputFiles({ name: "uat.yaml", mimeType: "text/yaml", buffer: Buffer.from(RIGHT) });
  await page.getByRole("button", { name: "Compare", exact: true }).click();
  await expect(differences(page)).toBeVisible();
  const card = page.getByRole("region", { name: "File A", exact: true });
  await expect(card).toContainText("prod.yaml");
  await expect(card).toContainText("detected yaml");
  await expect(card).toContainText("sha256");
  await expect(card.getByRole("button", { name: "Replace" })).toBeVisible();
  await page.getByLabel("File A file").setInputFiles({ name: "other.yaml", mimeType: "text/yaml", buffer: Buffer.from(RIGHT) });
  await expect(card).toContainText("other.yaml");
  await expect(card).not.toContainText("detected yaml");
  await expect(differences(page).getByRole("row", { name: /payments\.api_key/ })).toBeVisible();
});

test("a file past the limit is refused before it is sent", async ({ page }) => {
  await page.goto("/tools/compare-files");
  const sent: string[] = [];
  page.on("request", (r) => {
    if (r.method() === "POST") sent.push(r.url());
  });
  await page.getByLabel("File A file").setInputFiles({ name: "big.log", mimeType: "text/plain", buffer: Buffer.alloc(10_000_001, "x") });
  await expect(page.getByRole("region", { name: "File A", exact: true }).getByRole("alert")).toContainText("big.log is 10,000,001 bytes; the limit is 10,000,000 bytes per file.");
  await expect(page.getByRole("button", { name: "Compare", exact: true })).toBeDisabled();
  expect(sent).toEqual([]);
});

test("a binary file is refused with a clear message", async ({ page }) => {
  await page.goto("/tools/compare-files");
  await page.getByLabel("File A file").setInputFiles({ name: "a.bin", mimeType: "application/octet-stream", buffer: Buffer.from([65, 0, 66]) });
  await expect(page.getByRole("region", { name: "File A", exact: true }).getByRole("alert")).toContainText("a.bin looks like a binary file");
});

test("a refusal from the server is shown, and the person can try again", async ({ page }) => {
  await compareTwo(page, "invalid\n", RIGHT);
  await expect(page.getByRole("alert")).toContainText("a is not valid YAML (line 2)");
  await paste(page, "File A", LEFT);
  await page.getByRole("button", { name: "Compare", exact: true }).click();
  await expect(differences(page)).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("no differences says so", async ({ page }) => {
  await compareTwo(page, "same\n", "same\n");
  await expect(differences(page).getByText("No differences.")).toBeVisible();
});

test("the Production banner is not shown on a tool, which uses no application", async ({ page }) => {
  await page.goto("/inspect");
  await expect(page.locator(".prod-banner")).toBeVisible();
  await page.getByRole("link", { name: "Compare files" }).click();
  await expect(page.locator(".prod-banner")).toHaveCount(0);
  await expect(page.getByText("not used by this tool")).toBeVisible();
  await expect(page.getByLabel("Environment")).toHaveCount(0);
});

test("a kill switch banner still shows on a tool", async ({ page }) => {
  await page.goto("/connectors");
  await page.getByRole("button", { name: "wallet_db" }).click();
  await page.getByRole("checkbox", { name: "Kill switch" }).click();
  await page.getByRole("dialog").getByLabel("Reason").fill("on fire");
  await page.getByRole("dialog").getByRole("button", { name: "Stop reads" }).click();
  await page.getByRole("link", { name: "Compare files" }).click();
  await expect(page.getByRole("alert", { name: "Kill switches on" })).toBeVisible();
});
