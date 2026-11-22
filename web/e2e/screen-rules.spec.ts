// Author: @SaadZarook
import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { expectNoOverflow } from "./overflow";
import { copyOverCode, monoHeadings, pastTheEdge, shellAt, unequalControlRows, unevenChips } from "./rules";

// Every screen against the UI foundation's rules (#105). Since PR 5 (#146) every screen is on
// web/src/ui and none breaks a rule, so each test here is a plain one: a screen that breaks a rule
// fails the run. The components hold the rules on their own in ui-rules.spec.ts.

import { open, SCREENS, VALIDATE } from "./screens";

for (const screen of SCREENS) {
  test.describe(screen.name, () => {
    test("rule 6: nothing scrolls sideways at 320, 390, 768 and 1024", async ({ page }) => {
      for (const width of [320, 390, 768, 1024]) {
        await open(page, screen, width);
        await expectNoOverflow(page, `${screen.name} at ${width}`);
        expect(await pastTheEdge(page), `${screen.name} at ${width}`).toEqual([]);
      }
    });

    test("rule 2: every control in a form row has the same height", async ({ page }) => {
      for (const width of [1440, 1024, 390]) {
        await open(page, screen, width);
        expect(await unequalControlRows(page), `${screen.name} at ${width}`).toEqual([]);
      }
    });

    test("rule 4: no heading or table header is in Plex Mono", async ({ page }) => {
      await open(page, screen, 1440);
      expect(await monoHeadings(page), screen.name).toEqual([]);
    });
  });
}

test("rule 5: from 1024 px, the sidebar and no Menu button, and a top bar of at most two rows", async ({ page }) => {
  for (const width of [1024, 1280, 1440]) {
    await open(page, VALIDATE, width);
    expect(await shellAt(page), `at ${width}`).toEqual({ sidebar: true, menu: false, topBarRows: expect.any(Number) });
    expect((await shellAt(page)).topBarRows, `at ${width}`).toBeLessThanOrEqual(2);
  }
});

test("rule 5: below 1024 px, a Menu button instead of the sidebar, and a top bar of at most two rows", async ({ page }) => {
  for (const width of [1023, 768, 640, 639, 390, 320]) {
    await open(page, VALIDATE, width);
    const shell = await shellAt(page);
    expect(shell, `at ${width}`).toEqual({ sidebar: false, menu: true, topBarRows: expect.any(Number) });
    expect(shell.topBarRows, `at ${width}`).toBeLessThanOrEqual(2);
  }
});

test("rule 5: below 640 px the selects take the second row, and Search and the user stay together", async ({ page }) => {
  for (const width of [639, 390, 320]) {
    await open(page, VALIDATE, width);
    const bar = page.getByRole("banner");
    const top = async (name: string) => (await bar.getByRole(name === "Environment" ? "combobox" : "button", { name }).boundingBox())?.y ?? -1;
    const [menu = -1, search = -2, application = -3, environment = -4] = await Promise.all(["Menu", "Search", "Application", "Environment"].map(top));
    expect(search, `at ${width}`).toBe(menu);
    expect(application, `at ${width}`).toBeGreaterThan(menu);
    expect(environment, `at ${width}`).toBe(application);
    const user = await bar.getByLabel(/^Signed in as/).boundingBox();
    expect(Math.abs((user?.y ?? 0) + (user?.height ?? 0) / 2 - (menu + 20)), `the user beside Search at ${width}`).toBeLessThanOrEqual(4);
  }
});

test("the menu opens the navigation, goes where it is told and closes, and passes axe", async ({ page }) => {
  await open(page, VALIDATE, 390);
  const menu = page.getByRole("button", { name: "Menu", exact: true });
  await menu.click();
  const sheet = page.getByRole("dialog", { name: "ProbeOwl" });
  await expect(sheet.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Validate" })).toHaveAttribute("aria-current", "page");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await sheet.getByRole("link", { name: "Connectors" }).click();
  await expect(sheet).toHaveCount(0);
  await expect(page).toHaveURL(/\/connectors$/);
  await expect(page.getByRole("heading", { level: 1, name: "Connectors" })).toBeVisible();
  await menu.click();
  await page.keyboard.press("Escape");
  await expect(sheet).toHaveCount(0);
  await expect(menu).toBeFocused();
});

test("in Production the folded top bar still shows it in red, at 390 and 320 px", async ({ page }) => {
  for (const width of [390, 320]) {
    await open(page, VALIDATE, width);
    await page.getByRole("button", { name: "Application" }).click();
    await page.getByRole("button", { name: /Wallet/ }).last().click();
    const environment = page.getByRole("banner").getByLabel("Environment");
    await environment.selectOption({ label: "Production" });
    await expect(environment).toBeInViewport();
    const look = await environment.evaluate((el) => {
      const style = getComputedStyle(el);
      return { color: style.color, border: style.borderTopColor, weight: style.fontWeight };
    });
    expect(look, `at ${width}`).toEqual({ color: "rgb(180, 35, 24)", border: "rgb(180, 35, 24)", weight: "600" });
    await expect(environment.locator("option:checked")).toHaveText("Production");
  }
});

// The run screens moved in PR 3 (#138): rules 7 and 8 on them too.
for (const name of ["a Validate run", "an Inspect run"]) {
  test(`rule 7: on ${name}, Copy stays clear of the command at 390 and 1024 px`, async ({ page }) => {
    const screen = SCREENS.find((s) => s.name === name);
    if (screen === undefined) throw new Error(`${name} is not in SCREENS`);
    for (const width of [390, 1024]) {
      await open(page, screen, width);
      const copy = page.getByRole("button", { name: "Copy command" });
      await expect(copy).toBeVisible();
      expect(await copyOverCode(page), `${name} at ${width}`).toEqual([]);
    }
  });
}

test("rule 8: Validate and Inspect say what they still need while disabled", async ({ page }) => {
  await open(page, VALIDATE, 1440);
  const validate = page.getByRole("button", { name: "Validate", exact: true });
  await expect(validate).toBeDisabled();
  await expect(validate).toHaveAccessibleDescription("To validate: an ID, a reason, a reference.");
  await page.getByLabel("Any ID").fill("tp_91f2c7");
  await page.getByLabel("Reason").selectOption("incident");
  await expect(validate).toHaveAccessibleDescription("To validate: a reference.");
  await page.getByLabel(/^Reference/).fill("WALLET-482");
  await expect(validate).toBeEnabled();
  await expect(page.getByText(/^To validate:/)).toHaveCount(0);

  await open(page, { name: "Inspect", path: "/inspect" }, 1440);
  const inspect = page.getByRole("button", { name: "Inspect", exact: true });
  await expect(inspect).toBeDisabled();
  await expect(page.getByText(/^To inspect: an identifier, a reason/)).toBeVisible();
  // An application with no Inspect probe says so at the button too, not only above it.
  await page.getByRole("button", { name: "Application" }).click();
  await page.getByRole("button", { name: /Wallet/ }).last().click();
  await expect(page.getByText(/^To inspect: .*a probe\.$/)).toBeVisible();
});

test("rule 8: the disabled reason asks for a reference only in Production", async ({ page }) => {
  for (const [path, button, id, application] of [
    ["/validate", "Validate", "Any ID", /Wallet/],
    ["/inspect", "Inspect", "Identifier", /Card access/],
  ] as const) {
    const verb = button.toLowerCase();
    const what = id === "Any ID" ? "an ID" : "an identifier";
    await open(page, { name: button, path }, 1440);
    await page.getByRole("button", { name: "Application" }).click();
    await page.getByRole("button", { name: application }).last().click();
    const submit = page.getByRole("button", { name: button, exact: true });
    const environment = page.getByRole("main").getByLabel("Environment");
    // The environment carries across screens (#94): start each form in Production.
    await environment.selectOption("prod");
    await expect(submit).toHaveAccessibleDescription(`To ${verb}: ${what}, a reason, a reference.`);
    await expect(page.getByLabel("Reference", { exact: true })).toBeVisible();

    for (const other of ["uat", "dev"]) {
      if ((await environment.locator(`option[value="${other}"]`).count()) === 0) continue;
      await environment.selectOption(other);
      await expect(page.getByLabel("Reference (optional)", { exact: true })).toBeVisible();
      await expect(submit).toHaveAccessibleDescription(`To ${verb}: ${what}, a reason.`);
      await page.getByLabel(id).fill("C204518");
      await page.getByLabel("Reason").selectOption({ index: 1 });
      // Optional here: with no reference it can be pressed, and says nothing.
      await expect(submit).toBeEnabled();
      await expect(page.getByText(new RegExp(`^To ${verb}:`))).toHaveCount(0);
      await page.getByLabel(id).fill("");
      await page.getByLabel("Reason").selectOption("");
    }
  }
});

// Setup and connections moved in PR 4 (#141), with one chip everywhere: rule 9 on the screens that show chips.
for (const name of ["Connectors", "Tables", "Runs in UAT", "a Validate run", "an Inspect run", "a healthy comparison"]) {
  test(`rule 9: on ${name}, every chip is 24 px and shows all its text, at 1440 and 390 px`, async ({ page }) => {
    const screen = SCREENS.find((s) => s.name === name);
    if (screen === undefined) throw new Error(`${name} is not in SCREENS`);
    for (const width of [1440, 390]) {
      await open(page, screen, width);
      expect(await page.locator("main [data-outcome], main [data-health], main [data-status], main .label-chip").count(), `${name} has chips`).toBeGreaterThan(0);
      expect(await unevenChips(page), `${name} at ${width}`).toEqual([]);
    }
  });
}

// The probe editor moved in PR 5 (#146). On a phone the YAML reads and scrolls; writing it is for a
// wider screen, and the screen says so. #96's indent fix still holds there.
test("at 390 px the YAML editor says it's for a wider screen, scrolls inside itself, and keeps #96's indent", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 900 });
  await page.goto("/probes?new=validate&entity=topup");
  const form = page.getByRole("form", { name: "New probe" });
  await form.getByLabel("Probe id").fill("topup_narrow");
  await form.getByLabel("Title").fill("Narrow");
  await form.getByRole("button", { name: "Start the draft" }).click();
  await expect(page.getByRole("note").filter({ hasText: "Editing is designed for a wider screen" })).toBeVisible();
  const yaml = page.getByLabel("Probe YAML");
  await yaml.getByText("- expect: count(record) >= 1").click();
  await page.keyboard.press("End");
  await page.keyboard.press("Enter");
  await page.keyboard.type("- expect: count(ledger where direction == 'credit') == count(ledger where direction == 'debit')");
  const lines = (await yaml.innerText()).split(String.fromCharCode(10));
  expect(lines).toContain("  - expect: count(record) >= 1");
  expect(lines.some((l) => l.startsWith("  - expect: count(ledger where direction"))).toBe(true);
  // The long line scrolls inside the editor; the page itself never does.
  const scroller = page.locator(".cm-scroller").first();
  expect(await scroller.evaluate((el) => el.scrollWidth > el.clientWidth)).toBe(true);
  await expectNoOverflow(page, "the probe editor at 390");
  expect(await pastTheEdge(page)).toEqual([]);
  // Scrolled to, its line numbers stand level with its lines.
  await yaml.scrollIntoViewIfNeeded();
  const level = await page.evaluate(() => {
    const tops = (selector: string) => Array.from(document.querySelectorAll(selector)).slice(0, 5).map((el) => Math.round(el.getBoundingClientRect().top));
    return { lines: tops(".cm-line"), numbers: tops(".cm-lineNumbers .cm-gutterElement:not(:first-child)") };
  });
  expect(level.numbers).toEqual(level.lines);
  await page.setViewportSize({ width: 1024, height: 900 });
  await expect(page.getByRole("note").filter({ hasText: "Editing is designed for a wider screen" })).toBeHidden();
});
