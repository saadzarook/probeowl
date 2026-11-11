// Author: @SaadZarook
import { expect, test } from "@playwright/test";

// e2e.yml serves a copy of the examples here, so a save never touches the checkout.
const PROBE = "e2e_saved_probe";

// Plans are read from real databases, slower than the default 30 s allows.
test.setTimeout(120_000);

test("a new probe in UAT: the diff, then confirm; confirming again from that preview is a 409", async ({ page }) => {
  await page.goto("/probes");
  await page.getByRole("button", { name: "Application" }).click();
  await page.getByRole("button", { name: /Wallet/ }).last().click();
  await expect(page.getByRole("button", { name: "Application" })).toContainText("Wallet");
  await page.getByLabel("Environment").first().selectOption("uat");
  await page.getByRole("button", { name: "New probe" }).click();
  const form = page.getByRole("form", { name: "New probe" });
  await form.getByLabel("Probe id").fill(PROBE);
  await form.getByLabel("Title").fill("A live save");
  await form.getByRole("button", { name: "Start the draft" }).click();

  const yaml = page.getByLabel("Probe YAML");
  const save = page.getByRole("button", { name: "Save", exact: true });
  await expect(save).toBeEnabled({ timeout: 60_000 });

  // Two PUTs with one base: the real file changes between them.
  const bases: string[] = [];
  page.on("response", (r) => {
    if (r.request().method() === "PUT" && r.url().includes(`/probes/${PROBE}`) && r.status() === 200) {
      void r.json().then((c: { base: string }) => bases.push(c.base));
    }
  });

  await save.click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByLabel(`Diff of wallet/probes/${PROBE}.yaml`)).toBeVisible({ timeout: 60_000 });
  await expect(dialog.getByRole("button", { name: "Confirm" })).toBeEnabled();
  await dialog.getByRole("button", { name: "Confirm" }).click();
  await expect(dialog.getByRole("status")).toContainText(`Saved wallet/probes/${PROBE}.yaml`, { timeout: 60_000 });
  await expect(dialog.getByRole("status")).toContainText("Commit it when you're ready");

  // The same preview's base, sent again as the dialog would: the file is no longer as it was.
  await expect.poll(() => bases.length).toBeGreaterThan(0);
  const body = { probe: await yaml.innerText(), environment: "uat", base: bases[0] };
  const again = await page.request.put(`/api/applications/wallet/probes/${PROBE}`, {
    data: body,
    headers: { "X-ProbeOwl-Request": "1" },
  });
  expect(again.status()).toBe(409);
  expect((await again.json()).detail).toContain("changed since its preview");
});

test("an existing probe opens its real file: an edit changes only that line and keeps its comments", async ({ page }) => {
  await page.goto("/probes");
  await page.getByRole("button", { name: "Application" }).click();
  await page.getByRole("button", { name: /Wallet/ }).last().click();
  await expect(page.getByRole("button", { name: "Application" })).toContainText("Wallet");
  await page.getByLabel("Environment").first().selectOption("uat");
  await page.getByLabel("Open probe").selectOption("topup_completed");

  const yaml = page.getByLabel("Probe YAML");
  await expect(yaml).toContainText("Did a top-up complete everywhere?");
  await expect(page.getByText("Nothing to save")).toBeVisible();
  await page.getByLabel("Owner").fill("e2e-team");
  const save = page.getByRole("button", { name: "Save", exact: true });
  await expect(save).toBeEnabled({ timeout: 60_000 });
  await save.click();

  const dialog = page.getByRole("dialog");
  const diff = dialog.getByLabel("Diff of wallet/probes/topup_completed.yaml");
  await expect(diff).toContainText("+owner: e2e-team", { timeout: 60_000 });
  await expect(diff).toContainText("-owner: payments-team");
  const changed = (await diff.locator('[data-kind="add"], [data-kind="remove"]').allInnerTexts()).length;
  expect(changed).toBe(2);
  await dialog.getByRole("button", { name: "Confirm" }).click();
  await expect(dialog.getByRole("status")).toContainText("Saved wallet/probes/topup_completed.yaml", { timeout: 60_000 });
});

