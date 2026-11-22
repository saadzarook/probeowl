// Author: @SaadZarook
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page, type Response } from "@playwright/test";
import { open, SCREENS, VALIDATE } from "../e2e/screens";

// Every screen under owl serve's own policy (#148): style-src 'self' plus this page's nonce, never
// 'unsafe-inline'. A refused style shows as a securitypolicyviolation event and a console error;
// either fails the test. In light and dark, since the dark theme is on (#47). SHOTS=<folder> also
// saves each screen, for the PR's dark screenshots.

const THEMES = ["light", "dark"] as const;
const SHOTS = process.env["SHOTS"];

/** Collect every CSP violation the page reports, from its first script on. */
async function watch(page: Page): Promise<() => Promise<string[]>> {
  const console: string[] = [];
  page.on("console", (message) => {
    if (/Content Security Policy|Refused to apply/i.test(message.text())) console.push(message.text());
  });
  await page.addInitScript(() => {
    const seen: string[] = [];
    (window as unknown as { cspViolations: string[] }).cspViolations = seen;
    document.addEventListener("securitypolicyviolation", (event) =>
      seen.push(`${event.violatedDirective} ${event.blockedURI || "inline"} ${event.sample}`.trim()),
    );
  });
  return async () => [...(await page.evaluate(() => (window as unknown as { cspViolations: string[] }).cspViolations)), ...console];
}

/** The page's policy, checked to be the strict one, and the nonce it names. */
function strictPolicy(response: Response | null): string {
  const policy = response?.headers()["content-security-policy"] ?? "";
  expect(policy).toMatch(/style-src 'self' 'nonce-[A-Za-z0-9_-]{22}';/);
  expect(policy).toContain("script-src 'self';");
  expect(policy).not.toContain("unsafe-inline");
  return /'nonce-([A-Za-z0-9_-]+)'/.exec(policy)?.[1] ?? "";
}

for (const theme of THEMES) {
  test.describe(`${theme} theme`, () => {
    test.use({ colorScheme: theme });

    test(`the page is served under the strict policy and takes the ${theme} theme`, async ({ page }) => {
      strictPolicy(await page.goto("/inspect"));
      // The theme reaches the page: its background is the token's, light or dark.
      await page.getByRole("main").waitFor();
      const background = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
      expect(background).toBe(theme === "dark" ? "rgb(15, 18, 22)" : "rgb(246, 247, 249)");
    });

    for (const screen of SCREENS) {
      test(`${screen.name}: no CSP violation and no contrast failure at 1440 and 390, ${theme}`, async ({ page }) => {
        test.setTimeout(120_000);
        const violations = await watch(page);
        for (const width of [1440, 390]) {
          await open(page, screen, width);
          expect(await violations(), `${screen.name} at ${width}, ${theme}`).toEqual([]);
          // WCAG AA contrast on every text and chip shown (axe's color-contrast, 4.5:1 and 3:1 for large text).
          const contrast = await new AxeBuilder({ page }).withRules(["color-contrast"]).analyze();
          expect(
            contrast.violations.flatMap((v) => v.nodes.map((n) => `${n.target.join(" ")}: ${n.failureSummary ?? ""}`)),
            `${screen.name} at ${width}, ${theme}`,
          ).toEqual([]);
          if (SHOTS) {
            const name = screen.name.toLowerCase().replace(/[^a-z0-9]+/g, "-");
            await page.screenshot({ path: `${SHOTS}/${theme}-${name}-${width}.png`, fullPage: true });
          }
        }
      });
    }

    test(`the entity graph and its Outline: no CSP violation as boxes are laid out and selected, ${theme}`, async ({ page }) => {
      // The boxes are placed by style properties set through the DOM (#162): never a <style> or a
      // style attribute, which this policy refuses.
      const violations = await watch(page);
      for (const width of [1440, 390]) {
        await page.setViewportSize({ width, height: 900 });
        strictPolicy(await page.goto(`/ui-gallery?theme=${theme}`));
        const graph = page.getByRole("group", { name: "Entity graph of Top-up" });
        await graph.getByRole("button", { name: "Top-up record Found" }).focus();
        await page.keyboard.press("ArrowRight");
        await page.keyboard.press("ArrowDown");
        await expect(graph.getByRole("button", { name: "Webhook deliveries Missing" })).toBeFocused();
        // Laid out, not stacked at the corner: the policy did not drop the positions.
        const boxes = await graph.locator("[data-box]").evaluateAll((all) => all.map((b) => b.getBoundingClientRect().left));
        expect(new Set(boxes).size, `at ${width}`).toBeGreaterThan(1);
        await page.getByRole("list", { name: "Outline of Visitor" }).getByRole("button", { name: "Door controller Missing" }).click();
        expect(await violations(), `the graph at ${width}, ${theme}`).toEqual([]);
        const contrast = await new AxeBuilder({ page }).include('[aria-labelledby="graph-visitor"]').include('[aria-labelledby="graph-topup"]').withRules(["color-contrast"]).analyze();
        expect(contrast.violations.flatMap((v) => v.nodes.map((n) => n.target.join(" "))), `at ${width}, ${theme}`).toEqual([]);
      }
    });

    test(`the probe editor's CodeMirror is styled through the page's nonce, ${theme}`, async ({ page }) => {
      const violations = await watch(page);
      const nonce = strictPolicy(await page.goto("/probes"));
      await page.getByLabel("Open probe").selectOption("topup_completed");
      const yaml = page.getByLabel("Probe YAML");
      await expect(yaml).toContainText("probe: topup_completed");
      // CodeMirror's own <style>, applied: it carries the nonce, and its rules took effect.
      const styled = await page.evaluate((expected) => {
        const own = Array.from(document.querySelectorAll("style")).filter((s) => s.nonce === expected);
        const scroller = document.querySelector(".cm-scroller");
        const gutters = document.querySelector(".cm-gutters");
        return {
          withNonce: own.length,
          withoutNonce: Array.from(document.querySelectorAll("style")).filter((s) => s.nonce !== expected).length,
          scroller: scroller ? getComputedStyle(scroller).overflowX : "missing",
          gutters: gutters ? getComputedStyle(gutters).display : "missing",
        };
      }, nonce);
      expect(styled).toEqual({ withNonce: expect.any(Number), withoutNonce: 0, scroller: "auto", gutters: "flex" });
      expect(styled.withNonce).toBeGreaterThan(0);
      // And it edits: what is typed lands in the YAML.
      await yaml.getByText("- expect: count(record) >= 1").click();
      await page.keyboard.press("End");
      await page.keyboard.press("Enter");
      await page.keyboard.type("- expect: count(ledger) == 2");
      await expect(yaml).toContainText("- expect: count(ledger) == 2");
      expect(await violations()).toEqual([]);
    });

    test(`the Sheet's scroll lock holds the page through the nonce, ${theme}`, async ({ page }) => {
      const violations = await watch(page);
      await page.setViewportSize({ width: 390, height: 800 });
      strictPolicy(await page.goto("/validate"));
      await open(page, VALIDATE, 390);
      // open() loads the page afresh, with a nonce of its own: the one this page was given.
      const nonce = await page.evaluate(() => document.querySelector<HTMLMetaElement>('meta[property="csp-nonce"]')?.nonce ?? "");
      expect(nonce).toMatch(/^[A-Za-z0-9_-]{22}$/);
      const before = await page.evaluate(() => getComputedStyle(document.body).overflow);
      await page.getByRole("button", { name: "Menu", exact: true }).click();
      await expect(page.getByRole("dialog", { name: "ProbeOwl" })).toBeVisible();
      // react-remove-scroll's <style> applied: the page behind the open Sheet no longer scrolls.
      const locked = await page.evaluate(
        (expected) => ({
          overflow: getComputedStyle(document.body).overflow,
          lockStyle: Array.from(document.querySelectorAll("style")).some((s) => s.nonce === expected && /overflow:\s*hidden/.test(s.textContent ?? "")),
        }),
        nonce,
      );
      expect(before).not.toBe("hidden");
      expect(locked).toEqual({ overflow: "hidden", lockStyle: true });
      const contrast = await new AxeBuilder({ page }).withRules(["color-contrast"]).analyze();
      expect(contrast.violations).toEqual([]);
      await page.keyboard.press("Escape");
      await expect(page.getByRole("dialog")).toHaveCount(0);
      expect(await page.evaluate(() => getComputedStyle(document.body).overflow)).toBe(before);
      expect(await violations()).toEqual([]);
    });

    test(`the guide works under its own strict policy, search included, ${theme}`, async ({ page }) => {
      const violations = await watch(page);
      const response = await page.goto("/guide/");
      const policy = response?.headers()["content-security-policy"] ?? "";
      expect(policy).toContain("style-src 'self';");
      expect(policy).not.toContain("unsafe-inline");
      await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
      // Material's stylesheet applied: its header bar has a colour, not the browser's nothing.
      expect(await page.locator(".md-header").evaluate((el) => getComputedStyle(el).backgroundColor)).not.toBe("rgba(0, 0, 0, 0)");
      // Material's search: at this width its box is always shown; its worker answers. Wait until the
      // worker says it is ready, then type: Material queries on key presses, which fill() doesn't send.
      const search = page.locator("input.md-search__input");
      await search.focus();
      await expect(page.locator(".md-search-result__meta")).toHaveText(/Type to start searching/i, { timeout: 30_000 });
      await search.pressSequentially("connector");
      await expect(page.locator(".md-search-result__item").first()).toBeVisible();
      await expect(page.locator(".md-search-result__item").first()).toContainText(/connector/i);
      // Another page, under its own policy: its inline scripts allowed by their hashes, nothing else.
      const next = await page.goto("/guide/getting-started/");
      expect(next?.status()).toBe(200);
      expect(next?.headers()["content-security-policy"]).not.toContain("unsafe-inline");
      await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
      expect(await violations()).toEqual([]);
    });
  });
}

test("every chip and status colour pair meets WCAG AA (4.5:1) in both themes", async ({ page }) => {
  for (const theme of THEMES) {
    await page.emulateMedia({ colorScheme: theme });
    await page.goto(`/ui-gallery?theme=${theme}`);
    await page.getByRole("heading", { name: "UI components" }).waitFor();
    const pairs = await page.evaluate(() => {
      const style = getComputedStyle(document.documentElement);
      const token = (name: string) => style.getPropertyValue(`--ui-${name}`).trim();
      // The build shortens a colour where it can (#ffffff to #fff): read both forms.
      const rgb = (hex: string) => {
        const full = hex.length === 4 ? `#${[...hex.slice(1)].map((c) => c + c).join("")}` : hex;
        return [1, 3, 5].map((i) => parseInt(full.slice(i, i + 2), 16) / 255);
      };
      const lum = (hex: string) =>
        rgb(hex)
          .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4))
          .reduce((sum, c, i) => sum + c * ([0.2126, 0.7152, 0.0722][i] ?? 0), 0);
      const ratio = (a: string, b: string) => {
        const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x) as [number, number];
        return Math.round(((hi + 0.05) / (lo + 0.05)) * 100) / 100;
      };
      // Every text-on-fill pair the chips, labels, roles and status use, and text on the page and cards.
      const PAIRS: [string, string][] = [
        ["found-fg", "found-bg"], ["missing-fg", "missing-bg"], ["waiting-fg", "waiting-bg"], ["blocked-fg", "blocked-bg"],
        ["ambiguous-fg", "ambiguous-bg"], ["stale-fg", "stale-bg"], ["blue-fg", "blue-bg"], ["teal-fg", "teal-bg"],
        ["pink-fg", "pink-bg"], ["olive-fg", "olive-bg"], ["on-production", "production"], ["production-text", "card"],
        ["production-text", "production-bg"], ["foreground", "background"], ["foreground", "card"], ["muted", "background"],
        ["muted", "card"], ["primary-foreground", "primary"], ["primary", "card"], ["code-fg", "code-bg"],
        ["sidebar-foreground", "sidebar"], ["sidebar-muted", "sidebar"], ["primary", "avatar-bg"], ["foreground", "selected-bg"],
      ];
      return PAIRS.map(([fg, bg]) => ({ pair: `${fg} on ${bg}`, ratio: ratio(token(fg), token(bg)) }));
    });
    const failing = pairs.filter((p) => !(p.ratio >= 4.5));
    expect(failing, `${theme}: ${JSON.stringify(pairs)}`).toEqual([]);
  }
});
