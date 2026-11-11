// Author: @SaadZarook
import { expect, type Page } from "@playwright/test";

/** The page does not scroll sideways: if it does, the elements past the right edge are named. */
export async function expectNoOverflow(page: Page, where: string): Promise<void> {
  const past = await page.evaluate(() => {
    const width = document.documentElement.clientWidth;
    if (document.documentElement.scrollWidth <= width) return [];
    return Array.from(document.querySelectorAll("main *"))
      .filter((el) => el.getBoundingClientRect().right > width + 1 && el.getBoundingClientRect().width > 0)
      .slice(0, 5)
      .map((el) => `${el.tagName.toLowerCase()}.${String((el as HTMLElement).className).split(" ")[0] ?? ""} "${(el as HTMLElement).innerText.slice(0, 40)}"`);
  });
  expect(past, `${where} scrolls sideways; past the right edge: ${past.join(" | ")}`).toEqual([]);
}
