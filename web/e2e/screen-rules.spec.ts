// Author: @SaadZarook
import { expect, test, type Page } from "@playwright/test";
import { expectNoOverflow } from "./overflow";
import { monoHeadings, pastTheEdge, shellAt, unequalControlRows } from "./rules";

// Every screen against the UI foundation's rules (#105). A screen that breaks a rule today is
// marked with the PR that moves it onto web/src/ui: the test is expected to fail until then, and
// fails the run the moment it passes, so the mark is taken off in the PR that fixes it.
// The rules for chips, code blocks and disabled buttons are tested on each screen as it moves;
// web/src/ui holds them already (ui-rules.spec.ts).

type Screen = { name: string; path: string; ready?: (page: Page) => Promise<void> };

const SCREENS: Screen[] = [
  { name: "Inspect", path: "/inspect" },
  { name: "Validate", path: "/validate" },
  {
    name: "a Validate run",
    path: "/validate",
    ready: async (page) => {
      await page.getByLabel("Any ID").fill("tp_91f2c7");
      await page.getByLabel("Reason").selectOption("incident");
      await page.getByLabel(/^Reference/).fill("WALLET-482");
      await page.getByRole("button", { name: "Validate" }).click();
      await page.getByRole("region", { name: "Run result" }).getByRole("button", { name: "Copy command" }).waitFor();
    },
  },
  { name: "Health", path: "/health" },
  { name: "Probes", path: "/probes" },
  { name: "Knowledge base", path: "/knowledge-base" },
  { name: "Tables", path: "/tables" },
  { name: "Connectors", path: "/connectors" },
  { name: "Add connection", path: "/connectors/new" },
  { name: "Runs", path: "/runs" },
  { name: "Audit", path: "/audit" },
  { name: "New entity", path: "/probes/new-entity" },
  { name: "Setup", path: "/setup" },
];

const VALIDATE: Screen = { name: "Validate", path: "/validate" };

// Which rule each screen breaks today, and the PR of the phase plan that moves it (#105).
// The numbers are the audit's items in #105.
const RUN_SCREENS = "PR 3, the run screens";
const SETUP = "PR 4, setup and connections";
const EDITORS = "PR 5, the editors and the rest";
const UNTIL: Record<string, Partial<Record<"overflow" | "heights" | "mono", string>>> = {
  Inspect: { heights: RUN_SCREENS }, // 10
  Validate: { heights: RUN_SCREENS }, // 8
  "a Validate run": { heights: RUN_SCREENS, mono: RUN_SCREENS }, // 8, 15
  Tables: { overflow: SETUP }, // 4
  Connectors: { overflow: SETUP }, // 4
  "Add connection": { overflow: SETUP }, // 12: Save's reason runs past the edge at 390
  Probes: { mono: EDITORS }, // 15: the probe editor's h1
  Audit: { overflow: EDITORS }, // 4
};
const SHELL = "PR 2, the shell"; // 1, 2, 3

async function open(page: Page, screen: Screen, width: number): Promise<void> {
  await page.setViewportSize({ width, height: 900 });
  await page.goto(screen.path);
  await page.getByRole("main").waitFor();
  // Measured once its data has loaded: the mocks answer through the service worker, as requests.
  await page.waitForLoadState("networkidle");
  await screen.ready?.(page);
  await page.waitForLoadState("networkidle");
}

for (const screen of SCREENS) {
  test.describe(screen.name, () => {
    test("rule 6: nothing scrolls sideways at 320, 390, 768 and 1024", async ({ page }) => {
      const until = UNTIL[screen.name]?.overflow;
      test.fail(until !== undefined, `until ${until}`);
      for (const width of [320, 390, 768, 1024]) {
        await open(page, screen, width);
        await expectNoOverflow(page, `${screen.name} at ${width}`);
        expect(await pastTheEdge(page), `${screen.name} at ${width}`).toEqual([]);
      }
    });

    test("rule 2: every control in a form row has the same height", async ({ page }) => {
      const until = UNTIL[screen.name]?.heights;
      test.fail(until !== undefined, `until ${until}`);
      for (const width of [1440, 1024, 390]) {
        await open(page, screen, width);
        expect(await unequalControlRows(page), `${screen.name} at ${width}`).toEqual([]);
      }
    });

    test("rule 4: no heading or table header is in Plex Mono", async ({ page }) => {
      const until = UNTIL[screen.name]?.mono;
      test.fail(until !== undefined, `until ${until}`);
      await open(page, screen, 1440);
      expect(await monoHeadings(page), screen.name).toEqual([]);
    });
  });
}

test("rule 5: from 1024 px, the sidebar and no Menu button, and a top bar of at most two rows", async ({ page }) => {
  test.fail(true, `until ${SHELL}`);
  for (const width of [1024, 1440]) {
    await open(page, VALIDATE, width);
    expect(await shellAt(page), `at ${width}`).toEqual({ sidebar: true, menu: false, topBarRows: expect.any(Number) });
    expect((await shellAt(page)).topBarRows, `at ${width}`).toBeLessThanOrEqual(2);
  }
});

test("rule 5: below 1024 px, a Menu button instead of the sidebar, and a top bar of at most two rows", async ({ page }) => {
  test.fail(true, `until ${SHELL}`);
  for (const width of [1023, 390]) {
    await open(page, VALIDATE, width);
    const shell = await shellAt(page);
    expect(shell, `at ${width}`).toEqual({ sidebar: false, menu: true, topBarRows: expect.any(Number) });
    expect(shell.topBarRows, `at ${width}`).toBeLessThanOrEqual(2);
  }
});
