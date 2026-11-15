// Author: @SaadZarook
import type { Page } from "@playwright/test";

// The UI foundation's rules (#105), measured in the page. Each returns what breaks the rule,
// named so a failure says where: an empty list means the rule holds.

/** Rule 2: in each row of a form, every input, select and button has the same height (±1 px). */
export async function unequalControlRows(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const controls = Array.from(
      document.querySelectorAll<HTMLElement>(
        "main input:not([type=checkbox]):not([type=radio]):not([type=hidden]):not([type=file]), main select, main button",
      ),
    ).filter((el) => el.getBoundingClientRect().height > 0);
    const name = (el: HTMLElement) => {
      const labelled = el instanceof HTMLInputElement || el instanceof HTMLSelectElement ? el.labels?.[0]?.innerText : undefined;
      return `${el.tagName.toLowerCase()} "${(el.getAttribute("aria-label") || labelled || el.innerText).trim().slice(0, 24)}"`;
    };
    // Controls of one form, or one toolbar, whose boxes overlap vertically are a row.
    const groups = new Map<Element, HTMLElement[]>();
    for (const el of controls) {
      const scope = el.closest("form, fieldset, [role=search], [role=toolbar]") ?? el.parentElement?.parentElement ?? document.body;
      groups.set(scope, [...(groups.get(scope) ?? []), el]);
    }
    const broken: string[] = [];
    for (const members of groups.values()) {
      const sorted = members.sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
      let row: HTMLElement[] = [];
      let bottom = -Infinity;
      const check = () => {
        const fields = row.filter((el) => el.tagName !== "BUTTON");
        if (row.length < 2 || fields.length === 0) return;
        const heights = row.map((el) => el.getBoundingClientRect().height);
        if (Math.max(...heights) - Math.min(...heights) > 1)
          broken.push(row.map((el, i) => `${name(el)} ${Math.round(heights[i] ?? 0)}px`).join(", "));
      };
      for (const el of sorted) {
        const box = el.getBoundingClientRect();
        if (box.top >= bottom) {
          check();
          row = [];
        }
        row.push(el);
        bottom = Math.max(bottom, box.bottom);
      }
      check();
    }
    return broken;
  });
}

/** Rule 4: no heading, legend or table header is set in a monospace font. */
export async function monoHeadings(page: Page): Promise<string[]> {
  return page.evaluate(() =>
    Array.from(document.querySelectorAll<HTMLElement>("h1, h2, h3, h4, legend, th"))
      .filter((el) => el.getBoundingClientRect().height > 0)
      .filter((el) => /mono/i.test(getComputedStyle(el).fontFamily.split(",")[0] ?? ""))
      .map((el) => `${el.tagName.toLowerCase()} "${el.innerText.slice(0, 40)}"`),
  );
}

/** Rule 5: what the shell shows at this width: the sidebar, the Menu button, and the top bar's rows. */
export async function shellAt(page: Page): Promise<{ sidebar: boolean; menu: boolean; topBarRows: number }> {
  // The sidebar is the aside that holds the navigation: a screen may have an aside of its own.
  const sidebar = await page.locator("aside").filter({ has: page.getByRole("navigation") }).first().isVisible();
  const menu = await page.getByRole("button", { name: "Menu", exact: true }).isVisible();
  const topBarRows = await page.getByRole("banner").first().evaluate((bar) => {
    const tops = Array.from(bar.children)
      .map((child) => child.getBoundingClientRect())
      .filter((box) => box.height > 0 && box.width > 0)
      .map((box) => box.top)
      .sort((a, b) => a - b);
    // A child more than 8 px below the last row's top starts a new row.
    return tops.reduce<number[]>((rows, top) => (rows.length && top - (rows.at(-1) ?? 0) <= 8 ? rows : [...rows, top]), []).length;
  });
  return { sidebar, menu, topBarRows };
}

/** Rule 7: each Copy button's box stays clear of the code it copies. */
export async function copyOverCode(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const overlap = (a: DOMRect, b: DOMRect) => a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
    const code = Array.from(document.querySelectorAll("main pre, main code")).map((el) => el.getBoundingClientRect());
    return Array.from(document.querySelectorAll<HTMLElement>("main button"))
      .filter((button) => /^Copy/.test(button.innerText))
      .filter((button) => code.some((box) => overlap(box, button.getBoundingClientRect())))
      .map((button) => `"${button.innerText}" overlaps its code`);
  });
}

/** Rule 6, strictly: nothing in the page reaches past the screen's right edge, even inside a
 *  container that clips or scrolls it, as a table cut off at "Valid to" would. */
export async function pastTheEdge(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const width = document.documentElement.clientWidth;
    const past = Array.from(document.querySelectorAll<HTMLElement>("main *")).filter((el) => {
      const box = el.getBoundingClientRect();
      return box.width > 1 && box.height > 1 && box.right > width + 1;
    });
    // The outermost only: its children are past the edge because it is.
    return past
      .filter((el) => !past.some((other) => other !== el && other.contains(el)))
      .slice(0, 5)
      .map((el) => `${el.tagName.toLowerCase()} "${el.innerText.trim().slice(0, 40)}" to ${Math.round(el.getBoundingClientRect().right)}px`);
  });
}
