// Author: @SaadZarook
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "@playwright/test";
import { parse } from "yaml";

// First run against a real owl serve started in an empty folder with no store (#36). Its config
// folder is named in PROBEOWL_LIVE_FIRST_RUN_FOLDER; CI's live run serves the examples instead.
const folder = process.env["PROBEOWL_LIVE_FIRST_RUN_FOLDER"];

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
  await page.getByRole("link", { name: "Later" }).click();
  await expect(page.getByRole("button", { name: "Application" })).toContainText("Card access UAT");
});
