// Author: @SaadZarook
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { expectNoOverflow } from "./overflow";

// The entity graph and its Outline (#162), on the gallery (mocks/gallery/GraphGallery.tsx): one tab
// stop, arrow keys along the keys, the box's description outside its name, axe in both themes, and
// 390 px. The strict policy is checked in ui-rules (style-src 'self') and e2e-csp (owl serve's own).
// SHOTS=<folder> saves both examples at 1440 and 390, light and dark, for the PR.

const SHOTS = process.env["SHOTS"];

async function gallery(page: Page, width = 1440, theme = "light"): Promise<void> {
  await page.setViewportSize({ width, height: 900 });
  await page.goto(`/ui-gallery?theme=${theme}`);
  await page.getByRole("heading", { name: "Entity graph: Visitor" }).waitFor();
}

const graph = (page: Page, entity: string) => page.getByRole("group", { name: `Entity graph of ${entity}` });
const outline = (page: Page, entity: string) => page.getByRole("list", { name: `Outline of ${entity}` });
const selected = (page: Page, entity: string) => page.getByRole("region", { name: `Entity graph: ${entity}` }).locator("[data-selected]");

test("the graph is one tab stop: the selected box, and the next Tab leaves it", async ({ page }) => {
  await gallery(page);
  const boxes = graph(page, "Visitor").getByRole("button");
  await expect(boxes).toHaveCount(3);
  // Only the selected box is in the tab order.
  expect(await boxes.evaluateAll((all) => all.map((b) => (b as HTMLElement).tabIndex))).toEqual([0, -1, -1]);
  await page.getByRole("button", { name: "Visitor profile Found" }).first().focus();
  await page.keyboard.press("Tab");
  // Out of the graph, into the Outline's first button: not another box.
  await expect(outline(page, "Visitor").getByRole("button", { name: "Visitor profile Found" })).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(graph(page, "Visitor").getByRole("button", { name: "Visitor profile Found" })).toBeFocused();
});

test("← and → follow the keys, ↑ and ↓ move among siblings, Home goes to the root", async ({ page }) => {
  await gallery(page);
  const visitor = graph(page, "Visitor");
  const box = (name: string) => visitor.getByRole("button", { name });
  await box("Visitor profile Found").focus();
  await page.keyboard.press("ArrowRight");
  await expect(box("Cards Found")).toBeFocused();
  await expect(selected(page, "Visitor")).toHaveText("Selected: Cards");
  await expect(box("Cards Found")).toHaveAttribute("aria-pressed", "true");
  await expect(box("Cards Found")).toHaveAttribute("tabindex", "0");
  await page.keyboard.press("ArrowRight");
  await expect(box("Door controller Missing")).toBeFocused();
  await page.keyboard.press("ArrowRight"); // the end of the keys: stays
  await expect(box("Door controller Missing")).toBeFocused();
  await page.keyboard.press("ArrowLeft");
  await expect(box("Cards Found")).toBeFocused();
  await page.keyboard.press("Home");
  await expect(box("Visitor profile Found")).toBeFocused();

  const topup = graph(page, "Top-up");
  const step = (name: string) => topup.getByRole("button", { name });
  await step("Top-up record Found").focus();
  await page.keyboard.press("ArrowRight");
  await expect(step("Provider payment Found")).toBeFocused();
  await page.keyboard.press("ArrowDown");
  await expect(step("Webhook deliveries Missing")).toBeFocused();
  await page.keyboard.press("ArrowDown");
  await expect(step("Ledger entries Found")).toBeFocused();
  await page.keyboard.press("ArrowDown"); // the last sibling: stays
  await expect(step("Ledger entries Found")).toBeFocused();
  await page.keyboard.press("ArrowUp");
  await expect(step("Webhook deliveries Missing")).toBeFocused();
  await page.keyboard.press("ArrowLeft");
  await expect(step("Top-up record Found")).toBeFocused();
  // The page did not scroll for the arrow keys: each was handled by the graph.
  expect(await page.evaluate(() => window.scrollX)).toBe(0);
});

test("a box's name is its title and outcome, as it shows them; the rest is its description", async ({ page }) => {
  await gallery(page);
  for (const view of [graph(page, "Visitor"), outline(page, "Visitor")]) {
    const controller = view.getByRole("button", { name: "Door controller Missing", exact: true });
    await expect(controller).toHaveAccessibleName("Door controller Missing");
    await expect(controller).toHaveAccessibleDescription(
      "door_controller_api, GET /cards/{card_id}; one, for each row of Cards; key card_id from Cards; 1 of 2 cards: C104577 not on the controller",
    );
  }
  const ledger = graph(page, "Top-up").getByRole("button", { name: "Ledger entries Found", exact: true });
  await expect(ledger).toHaveAccessibleDescription("ledger_db, ledger_entries; many; key ledger_txn_id from Top-up record; 2 rows; fix: Confirm label: Transaction");
  await expect(graph(page, "Top-up").getByRole("button", { name: "Top-up record Found", exact: true })).toHaveAccessibleDescription(/; the root; 1 row$/);
  // The graph says how to move in it.
  await expect(graph(page, "Visitor")).toHaveAccessibleDescription("3 locations. Arrow keys follow the keys; Home goes to the root.");
});

test("each arrow is drawn with its key field", async ({ page }) => {
  await gallery(page);
  const fields = (entity: string) => graph(page, entity).locator("svg g[data-key]").evaluateAll((all) => all.map((g) => g.textContent));
  expect(await fields("Visitor")).toEqual(["visitor_profile_id", "card_id"]);
  expect(await fields("Top-up")).toEqual(["provider_payment_id", "provider_payment_id", "ledger_txn_id"]);
  // Each column top to bottom in the Outline's order, so ↓ moves down the screen as in the Outline.
  const y = async (name: string) => (await graph(page, "Top-up").getByRole("button", { name }).boundingBox())?.y ?? 0;
  const tops = await Promise.all(["Provider payment Found", "Webhook deliveries Missing", "Ledger entries Found"].map(y));
  expect(tops).toEqual([...tops].sort((a, b) => a - b));
  expect(new Set(tops).size).toBe(3);
  // Boxes left to right along the keys: each box starts right of the one its key comes from.
  const x = async (name: string) => (await graph(page, "Visitor").getByRole("button", { name }).boundingBox())?.x ?? 0;
  expect(await x("Cards Found")).toBeGreaterThan(await x("Visitor profile Found"));
  expect(await x("Door controller Missing")).toBeGreaterThan(await x("Cards Found"));
});

test("the Outline nests in key order and shares the graph's selection", async ({ page }) => {
  await gallery(page);
  const list = outline(page, "Visitor");
  await expect(list.getByRole("list", { name: "From Visitor profile" }).getByRole("button", { name: "Cards Found" })).toBeVisible();
  await expect(list.getByRole("list", { name: "From Cards" }).getByRole("button", { name: "Door controller Missing" })).toBeVisible();
  await list.getByRole("button", { name: "Door controller Missing" }).click();
  await expect(selected(page, "Visitor")).toHaveText("Selected: Door controller");
  // The graph's one tab stop moved with it.
  await expect(graph(page, "Visitor").getByRole("button", { name: "Door controller Missing" })).toHaveAttribute("tabindex", "0");
  await expect(list.getByRole("button", { name: "Door controller Missing" })).toHaveAttribute("aria-pressed", "true");
});

for (const theme of ["light", "dark"] as const) {
  for (const width of [1440, 390]) {
    test(`axe finds nothing in the graph or the Outline, ${theme} at ${width}`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: theme });
      await gallery(page, width, theme);
      await graph(page, "Visitor").getByRole("button", { name: "Cards Found" }).click();
      const axe = await new AxeBuilder({ page }).include('[aria-labelledby="graph-visitor"]').include('[aria-labelledby="graph-topup"]').analyze();
      expect(axe.violations).toEqual([]);
      if (SHOTS) await page.locator('[aria-labelledby="graph-visitor"]').locator("..").screenshot({ path: `${SHOTS}/graph-${theme}-${width}.png` });
    });
  }
}

test("at 390 px the graph scrolls sideways inside its own frame, never the page", async ({ page }) => {
  await gallery(page, 390);
  await expectNoOverflow(page, "the gallery at 390");
  const frame = page.locator("[data-scrolls=x]").filter({ has: graph(page, "Top-up") });
  const sizes = await frame.evaluate((el) => ({ scroll: el.scrollWidth, client: el.clientWidth }));
  expect(sizes.scroll).toBeGreaterThan(sizes.client);
  // A box keeps its full size: the text is not shrunk to fit, as fitView would.
  const ledger = await graph(page, "Top-up").locator("[data-box=ledger]").boundingBox();
  expect(ledger?.width).toBeCloseTo(244, 0);
  // Focusing a box off the frame's edge scrolls the frame to it.
  await graph(page, "Top-up").getByRole("button", { name: "Top-up record Found" }).focus();
  await page.keyboard.press("ArrowRight");
  await expect(graph(page, "Top-up").getByRole("button", { name: "Provider payment Found" })).toBeInViewport();
  await expectNoOverflow(page, "the gallery at 390, after scrolling the graph");
});

test("a box shows its outcome's whole reason, at 1440 and in the Outline at 390", async ({ page }) => {
  for (const width of [1440, 390]) {
    await gallery(page, width);
    const cut = await page.locator("[data-box]").evaluateAll((boxes) =>
      boxes.flatMap((box) => {
        const detail = box.querySelector<HTMLElement>("[data-detail]");
        if (!detail) return [];
        const b = box.getBoundingClientRect();
        const d = detail.getBoundingClientRect();
        const whole = detail.scrollWidth <= detail.clientWidth + 1 || getComputedStyle(detail).display === "inline";
        return whole && d.bottom <= b.bottom && d.right <= b.right && box.scrollHeight <= box.clientHeight + 1 ? [] : [`${box.getAttribute("data-box")}: ${detail.innerText}`];
      }),
    );
    expect(cut, `at ${width}`).toEqual([]);
    await expect(page.locator("[data-detail]").filter({ hasText: "nothing was read here" }).first()).toBeVisible();
  }
});

test("no arrow's label covers an arrow, and no two boxes overlap", async ({ page }) => {
  await gallery(page);
  for (const entity of ["Visitor", "Top-up", "Long reasons"]) {
    const covered = await graph(page, entity).evaluate((group) => {
      const keys = [...group.querySelectorAll<SVGGElement>("svg g[data-key]")];
      const labels = keys.map((g) => ({ field: g.dataset["key"], box: (g.querySelector("text") as SVGTextElement).getBBox() }));
      const found: string[] = [];
      for (const g of keys) {
        const path = g.querySelector("path") as SVGPathElement;
        for (let at = 0; at <= path.getTotalLength(); at += 2) {
          const p = path.getPointAtLength(at);
          for (const { field, box } of labels) {
            if (p.x >= box.x && p.x <= box.x + box.width && p.y >= box.y && p.y <= box.y + box.height) found.push(`${field} over the arrow at ${Math.round(p.x)},${Math.round(p.y)}`);
          }
        }
      }
      const boxes = [...group.querySelectorAll("[data-box]")].map((b) => b.getBoundingClientRect());
      boxes.forEach((a, i) => boxes.slice(i + 1).forEach((b) => {
        if (a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom) found.push("two boxes overlap");
      }));
      return [...new Set(found)];
    });
    expect(covered, entity).toEqual([]);
  }
});
