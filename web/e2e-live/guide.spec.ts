// Author: @SaadZarook
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test, type Page } from "@playwright/test";

// The guide as owl serve serves it, opened from every link the web app has to it: each page
// loads under its own strict policy, with no console error and nothing fetched from another site.
const SOURCE = fileURLToPath(new URL("../src", import.meta.url));

/** Every page="..." the web app's GuideLinks name, read from the source so none is missed. */
function linkedPages(): string[] {
  const found = new Set<string>();
  const walk = (folder: string) => {
    for (const entry of readdirSync(folder, { withFileTypes: true })) {
      const path = join(folder, entry.name);
      if (entry.isDirectory()) walk(path);
      else if (path.endsWith(".tsx")) {
        for (const match of readFileSync(path, "utf8").matchAll(/<GuideLink page="([^"]+)"/g)) found.add(match[1] ?? "");
      }
    }
  };
  walk(SOURCE);
  return [...found].sort();
}

/** Console errors (a refused script shows as one) and requests that left 127.0.0.1. */
function watch(page: Page): { errors: string[]; outside: string[] } {
  const seen = { errors: [] as string[], outside: [] as string[] };
  page.on("console", (message) => {
    if (message.type() === "error") seen.errors.push(message.text());
  });
  page.on("pageerror", (error) => seen.errors.push(error.message));
  page.on("request", (request) => {
    const host = new URL(request.url()).hostname;
    if (host !== "127.0.0.1" && host !== "localhost") seen.outside.push(request.url());
  });
  return seen;
}

test.beforeEach(async ({ request }) => {
  const home = await request.get("/guide/");
  test.skip(home.status() === 404, "the guide is not built: cd backend && uv run python guide_build.py");
});

test("the web app links to pages of the guide that exist", () => {
  expect(linkedPages().length).toBeGreaterThanOrEqual(5);
});

for (const linked of linkedPages()) {
  test(`the guide opens at ${linked} with no error and nothing from another site`, async ({ page }) => {
    const seen = watch(page);
    const [path, anchor] = linked.split("#");
    const response = await page.goto(`/guide/${linked}`);
    expect(response?.status()).toBe(200);
    expect(response?.headers()["content-security-policy"]).toContain("script-src 'self' 'sha256-");
    await expect(page.locator("article h1")).toBeVisible();
    if (anchor !== undefined) await expect(page.locator(`[id="${anchor}"]`)).toHaveCount(1);
    // The palette script ran: a refused inline script leaves Material without its scheme.
    await expect(page.locator("body")).toHaveAttribute("data-md-color-scheme", /default|slate/);
    await page.waitForLoadState("networkidle");
    expect(seen.errors, `on /guide/${path}`).toEqual([]);
    expect(seen.outside, `on /guide/${path}`).toEqual([]);
  });
}

test("the guide's search works offline, in its own worker", async ({ page }) => {
  const seen = watch(page);
  await page.goto("/guide/");
  await page.getByRole("textbox", { name: "Search" }).fill("replica");
  await expect(page.locator(".md-search-result__item").first()).toBeVisible();
  expect(seen.errors).toEqual([]);
  expect(seen.outside).toEqual([]);
});

test("the guide's fonts are the IBM Plex files it carries", async ({ page }) => {
  await page.goto("/guide/");
  const font = await page.locator("body").evaluate((body) => getComputedStyle(body).fontFamily);
  expect(font).toContain("IBM Plex Sans");
  await page.waitForLoadState("networkidle");
  expect(await page.evaluate(() => document.fonts.check('16px "IBM Plex Sans"'))).toBe(true);
});

test("a page that is not there is the guide's own 404, with its styles", async ({ page }) => {
  const seen = watch(page);
  const response = await page.goto("/guide/no-such-page/");
  expect(response?.status()).toBe(404);
  await expect(page.locator("article h1")).toContainText("404");
  await page.waitForLoadState("networkidle");
  expect(seen.outside).toEqual([]);
});
