// Author: @SaadZarook
import AxeBuilder from "@axe-core/playwright";
import { readFileSync } from "node:fs";
import { expect, test } from "@playwright/test";

const routes = ["Inspect", "Validate", "Health", "Probes", "Knowledge base", "Tables", "Connectors", "Runs"];

test("shell shows sidebar, top bar, Production banner and empty state", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/inspect$/);

  const nav = page.getByRole("navigation", { name: "Main" });
  for (const label of routes) {
    await expect(nav.getByRole("link", { name: label })).toBeVisible();
  }
  // The user owl serve answers /api/me with (s.khan in the mocks), never a fixture in the app.
  await expect(page.getByLabel("Signed in as s.khan")).toContainText("SK");
  await expect(page.getByText("Nothing inspected yet")).toBeVisible();
  await expect(page.locator(".prod-banner")).toContainText("Production");
});

test("each route renders a placeholder page", async ({ page }) => {
  await page.goto("/");
  for (const label of routes) {
    await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: label }).click();
    await expect(page.getByRole("heading", { level: 1, name: label })).toBeVisible();
  }
});

test("application selector searches by team and the banner follows the environment", async ({ page }) => {
  await page.goto("/inspect");
  await page.getByRole("button", { name: "Application" }).click();
  await page.getByRole("searchbox", { name: /Search applications/ }).fill("access-team");
  await page.getByRole("button", { name: /Card access/ }).last().click();
  await expect(page.getByRole("button", { name: "Application" })).toContainText("Card access");

  await page.getByLabel("Environment").first().selectOption("UAT");
  await expect(page.getByRole("status")).toHaveCount(0);
});

test("shell loads applications and environments through the generated client", async ({ page }) => {
  await page.goto("/inspect");
  await expect(page.getByRole("button", { name: "Application" })).toContainText("Wallet");
  await expect(page.getByLabel("Environment").first().locator("option")).toHaveText(["Production", "UAT"]);
});

test("runs answer 202 with a Location, then report running until finished", async ({ page }) => {
  await page.goto("/inspect");
  await expect(page.getByRole("button", { name: "Application" })).toContainText("Wallet");
  const result = await page.evaluate(async () => {
    const started = await fetch("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ application: "wallet", environment: "uat", probe: "topup_completed", value: "tp_91f2c7", investigation: { reason: "incident" } }),
    });
    const location = started.headers.get("Location") ?? "";
    const statuses: string[] = [];
    for (let i = 0; i < 5; i++) {
      const run = (await (await fetch(location)).json()) as { status: string };
      statuses.push(run.status);
    }
    return { code: started.status, location, statuses };
  });
  expect(result.code).toBe(202);
  expect(result.location).toMatch(/^\/api\/runs\/run_\d+$/);
  expect(result.statuses[0]).toBe("running");
  expect(result.statuses.at(-1)).toBe("failed");
});

test("has no axe violations", async ({ page }) => {
  await page.goto("/inspect");
  await expect(page.getByRole("heading", { level: 1, name: "Inspect" })).toBeVisible();
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations).toEqual([]);

  await page.getByRole("button", { name: "Application" }).click();
  const open = await new AxeBuilder({ page }).analyze();
  expect(open.violations).toEqual([]);
});

test("the sidebar shows ProbeOwl's version, as owl --version prints it, and the mode", async ({ page }) => {
  const pyproject = readFileSync(new URL("../../backend/pyproject.toml", import.meta.url), "utf8");
  const version = /^version = "([^"]+)"$/m.exec(pyproject)?.[1];
  await page.goto("/inspect");
  await expect(page.locator(".sidebar-footer")).toHaveText(`v${version ?? "?"} · local`);
});
