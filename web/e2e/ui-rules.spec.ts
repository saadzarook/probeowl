// Author: @SaadZarook
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { expectNoOverflow } from "./overflow";
import { copyOverCode, monoHeadings, pastTheEdge, unequalControlRows } from "./rules";

// The components of web/src/ui hold the UI foundation's rules (#105) from the start: on the gallery
// (mocks/gallery), which only mock builds serve. The screens' own rule tests are in screen-rules.

async function gallery(page: Page, width = 1440, theme = "light"): Promise<void> {
  await page.setViewportSize({ width, height: 900 });
  await page.goto(`/ui-gallery?theme=${theme}`);
  await page.getByRole("heading", { name: "UI components" }).waitFor();
}

const box = async (page: Page, label: string) => {
  const found = await page.getByLabel(label, { exact: true }).boundingBox();
  if (found === null) throw new Error(`${label} is not on the page`);
  return found;
};

test("rule 2: every control in a form row is 40 px, at every width", async ({ page }) => {
  for (const width of [1440, 1024, 390]) {
    await gallery(page, width);
    expect(await unequalControlRows(page), `at ${width}`).toEqual([]);
    for (const label of ["Environment", "Entity", "Any ID", "Reference"]) expect((await box(page, label)).height).toBeCloseTo(40, 0);
    expect((await page.getByRole("button", { name: "Validate" }).boundingBox())?.height).toBeCloseTo(40, 0);
    expect((await page.getByRole("button", { name: "Undo" }).boundingBox())?.height).toBeCloseTo(32, 0);
  }
});

test("rule 3: four columns from 1280, two from 640, one below; an error moves no other field", async ({ page }) => {
  const lefts = async () => new Set(await Promise.all(["Environment", "Entity", "Any ID", "Reference"].map(async (l) => (await box(page, l)).x)));
  await gallery(page, 1280);
  expect((await lefts()).size).toBe(4);
  await gallery(page, 640);
  expect((await lefts()).size).toBe(2);
  await gallery(page, 639);
  expect((await lefts()).size).toBe(1);

  for (const width of [1440, 390]) {
    await gallery(page, width);
    const before = await Promise.all(["Environment", "Entity", "Any ID", "Reference"].map((l) => box(page, l)));
    await expect(page.getByText("Accepts any identifier of a top-up")).toBeVisible();
    await page.getByRole("button", { name: "Show an error" }).click();
    await expect(page.getByRole("alert")).toHaveText("Enter an identifier");
    await expect(page.getByLabel("Any ID")).toHaveAttribute("aria-invalid", "true");
    await expect(page.getByLabel("Any ID")).toHaveAccessibleDescription("Enter an identifier");
    const after = await Promise.all(["Environment", "Entity", "Any ID", "Reference"].map((l) => box(page, l)));
    expect(after, `at ${width}`).toEqual(before);
  }
});

test("rule 4: headings and table headers are in Plex Sans; code is in Plex Mono", async ({ page }) => {
  await gallery(page);
  expect(await monoHeadings(page)).toEqual([]);
  const font = (locator: ReturnType<Page["locator"]>) => locator.evaluate((el) => getComputedStyle(el).fontFamily);
  expect(await font(page.getByRole("heading", { name: "UI components" }))).toMatch(/^"?IBM Plex Sans"?,/);
  expect(await font(page.locator("pre").first())).toMatch(/^"?IBM Plex Mono"?,/);
});

test("rule 6: nothing scrolls sideways; rows are a table from 768 px and cards below", async ({ page }) => {
  for (const width of [320, 390, 768, 1024]) {
    await gallery(page, width);
    await expectNoOverflow(page, `the gallery at ${width}`);
    expect(await pastTheEdge(page), `the gallery at ${width}`).toEqual([]);
  }
  await gallery(page, 767);
  await expect(page.getByRole("list", { name: "Bindings" })).toBeVisible();
  await expect(page.getByRole("table")).toHaveCount(0);
  await expect(page.getByRole("list", { name: "Bindings" }).getByRole("listitem")).toHaveCount(3);
  await expect(page.locator("li[data-highlight]")).toContainText("ledger_db");
  await gallery(page, 768);
  await expect(page.getByRole("table", { name: "Bindings" })).toBeVisible();
  await expect(page.getByRole("list", { name: "Bindings" })).toHaveCount(0);
  await expect(page.getByText("No bindings yet.")).toBeVisible();
  await expect(page.getByRole("columnheader")).toHaveText(["Connector", "Role", "Status"]);
  await expect(page.locator("tr[data-highlight]")).toHaveCount(1);
  await expect(page.locator("tr[data-highlight]")).toContainText("ledger_db");
});

test.describe("rule 7", () => {
  test.use({ permissions: ["clipboard-read", "clipboard-write"] });

  test("a 200-character command wraps in its block, with Copy below it under 640 px and beside it above", async ({ page }) => {
    for (const width of [390, 1024]) {
      await gallery(page, width);
      expect(await copyOverCode(page), `at ${width}`).toEqual([]);
      const code = await page.locator("pre").boundingBox();
      const copy = await page.getByRole("button", { name: "Copy command" }).boundingBox();
      if (code === null || copy === null) throw new Error("the code block is not on the page");
      if (width < 640) expect(copy.y).toBeGreaterThanOrEqual(code.y + code.height);
      else expect(copy.x).toBeGreaterThanOrEqual(code.x + code.width);
      await expectNoOverflow(page, `the code block at ${width}`);
    }
    await page.getByRole("button", { name: "Copy command" }).click();
    await expect(page.getByRole("status")).toHaveText("Copied.");
    expect(await page.evaluate(() => navigator.clipboard.readText())).toMatch(/^owl validate topup_completed tp_9f2c7a\S{191} --env production$/);
  });
});

test("rule 8: a disabled button says why, and a link that acts as a button looks like one", async ({ page }) => {
  await gallery(page);
  const save = page.getByRole("button", { name: "Save connection" });
  await expect(save).toBeDisabled();
  await expect(save).toHaveAccessibleDescription("Fill in every field first");
  await expect(page.getByText("Fill in every field first")).toBeVisible();
  // The components' own weights, never app.css's reset (#136): a button and Production's select.
  const weight = (locator: ReturnType<Page["locator"]>) => locator.evaluate((el) => getComputedStyle(el).fontWeight);
  expect(await weight(page.getByRole("button", { name: "Save", exact: true }))).toBe("600");
  expect(await weight(page.getByLabel("Environment"))).toBe("600");
  const link = page.getByRole("link", { name: "Add connection" });
  expect(await link.evaluate((el) => getComputedStyle(el).textDecorationLine)).toBe("none");
  expect((await link.boundingBox())?.height).toBeCloseTo(40, 0);
});

test("rule 9: every status chip is 24 px and shows all of its text", async ({ page }) => {
  for (const width of [1440, 320]) {
    await gallery(page, width);
    const chips = page.getByRole("list", { name: "Status chips" }).getByRole("listitem").locator("span");
    await expect(chips).toHaveCount(9);
    for (const chip of await chips.all()) {
      expect((await chip.boundingBox())?.height).toBeCloseTo(24, 0);
      expect(await chip.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true);
    }
  }
});

test("rule 10: axe finds nothing in light, dark, or dark by the system setting", async ({ page }) => {
  for (const [theme, scheme] of [["light", "light"], ["dark", "light"], ["system", "dark"], ["system", "light"]] as const) {
    await page.emulateMedia({ colorScheme: scheme });
    await gallery(page, 1440, theme);
    const results = await new AxeBuilder({ page }).analyze();
    expect(results.violations, `${theme} on a ${scheme} system`).toEqual([]);
  }
});

test("rule 10: the system's dark is the same as dark", async ({ page }) => {
  const tokens = () =>
    page.evaluate(() => {
      const style = getComputedStyle(document.documentElement);
      return Array.from(document.styleSheets)
        .flatMap((sheet) => Array.from(sheet.cssRules))
        .flatMap((rule) => (rule instanceof CSSStyleRule && rule.selectorText === ":root" ? Array.from(rule.style) : []))
        .filter((name) => name.startsWith("--ui-"))
        .map((name) => `${name}: ${style.getPropertyValue(name).trim()}`);
    });
  await page.emulateMedia({ colorScheme: "dark" });
  await gallery(page, 1440, "dark");
  const dark = await tokens();
  expect(dark.length).toBeGreaterThan(20);
  await gallery(page, 1440, "system");
  expect(await tokens()).toEqual(dark);
  await page.emulateMedia({ colorScheme: "light" });
  await gallery(page, 1440, "light");
  const light = await tokens();
  await gallery(page, 1440, "system");
  expect(await tokens()).toEqual(light);
  expect(light).not.toEqual(dark);
});

test("the sheet opens as a dialog, passes axe, and gives focus back when it closes", async ({ page }) => {
  for (const theme of ["light", "dark"]) {
    await gallery(page, 390, theme);
    const menu = page.getByRole("button", { name: "Menu" });
    await menu.click();
    const sheet = page.getByRole("dialog", { name: "ProbeOwl" });
    await expect(sheet.getByRole("navigation", { name: "Screens" }).getByRole("link")).toHaveText(["Inspect", "Validate", "Connectors"]);
    const results = await new AxeBuilder({ page }).analyze();
    expect(results.violations, theme).toEqual([]);
    await page.keyboard.press("Escape");
    await expect(sheet).toHaveCount(0);
    await expect(menu).toBeFocused();
    await menu.click();
    await sheet.getByRole("button", { name: "Close" }).click();
    await expect(sheet).toHaveCount(0);
  }
});

test("the components need no inline style: none is refused under style-src 'self' (#105)", async ({ page }) => {
  // owl serve still allows 'unsafe-inline' for CodeMirror; the components must not need it (PR 6).
  await page.route("**/ui-gallery*", async (route) => {
    const response = await route.fetch();
    await route.fulfill({ response, headers: { ...response.headers(), "content-security-policy": "style-src 'self'" } });
  });
  await page.addInitScript(() => {
    const refused: string[] = [];
    (window as unknown as { refused: string[] }).refused = refused;
    document.addEventListener("securitypolicyviolation", (event) => refused.push(`${event.violatedDirective}: ${event.sample || event.sourceFile}`));
  });
  await gallery(page);
  await page.getByRole("button", { name: "Show an error" }).click();
  await page.getByRole("button", { name: "Copy command" }).click();
  // Not the open Sheet: Radix's scroll lock (react-remove-scroll) adds a <style>, refused here, so
  // the page behind it would still scroll. It takes a nonce (get-nonce): that is PR 6's CSP change.
  expect(await page.evaluate(() => (window as unknown as { refused: string[] }).refused)).toEqual([]);
});
