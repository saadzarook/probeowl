// Author: @SaadZarook
import { writeFileSync } from "node:fs";
import { expect, test, type Locator, type Page } from "@playwright/test";

// The newcomer path's browser half, measured (#27): from the first page of `owl serve` after
// `owl init` to a first Validate on the card example. dev/measure/newcomer_baseline.sh serves the
// folder owl init wrote, with its schema imported, and names the result file here.
// Every step goes through the counter, so the count is what a person would do by hand:
// - action: a click, a choice in a select, a field filled, or a key that is not a character;
// - characters: every character typed into a field or the editor.
const out = process.env["PROBEOWL_MEASURE_OUT"];

test.skip(out === undefined, "measures the newcomer path: run dev/measure/newcomer_baseline.sh");
// A step that cannot happen fails within a minute, not at the test's own timeout.
test.use({ viewport: { width: 1440, height: 900 }, actionTimeout: 60_000 });

interface Segment {
  name: string;
  actions: number;
  characters: number;
  seconds: number;
}

/** Counts what a person would do in each segment, and how long the machine took. */
class Counter {
  readonly segments: Segment[] = [];
  private started = 0;

  constructor(private readonly page: Page) {}

  start(name: string) {
    this.segments.push({ name, actions: 0, characters: 0, seconds: 0 });
    this.started = Date.now();
  }

  end() {
    const segment = this.current();
    segment.seconds = Math.round((Date.now() - this.started) / 100) / 10;
  }

  async open(path: string) {
    this.current().actions += 1;
    await this.page.goto(path);
  }

  async click(target: Locator) {
    this.current().actions += 1;
    await target.click();
  }

  async select(target: Locator, option: string | { label: string }) {
    this.current().actions += 1;
    await target.selectOption(option);
  }

  async fill(target: Locator, text: string) {
    this.current().actions += 1;
    this.current().characters += text.length;
    await target.fill(text);
  }

  async press(key: string) {
    this.current().actions += 1;
    await this.page.keyboard.press(key);
  }

  async type(text: string) {
    this.current().characters += text.length;
    await this.page.keyboard.type(text);
  }

  private current(): Segment {
    const segment = this.segments.at(-1);
    if (segment === undefined) throw new Error("start a segment first");
    return segment;
  }
}

test("install to first Validate: the browser half, counted and timed", async ({ page }) => {
  test.setTimeout(600_000);
  const count = new Counter(page);

  count.start("Open the web app");
  await count.open("/");
  await expect(page.getByRole("button", { name: "Application" })).toContainText("Card baseline");
  count.end();

  // owl init imported the schema; each table's suggested label is confirmed as it stands.
  count.start("Confirm the table labels");
  await count.click(page.getByRole("link", { name: "Tables" }));
  for (const table of ["visitor_profile", "cards"]) {
    await count.click(page.getByRole("button", { name: new RegExp(`^${table}`) }));
    const detail = page.getByRole("region", { name: `Table ${table}` });
    await count.click(detail.getByRole("button", { name: "Preview" }));
    await expect(detail.getByLabel("Diff of card_baseline/catalog.yaml")).toBeVisible({ timeout: 60_000 });
    await count.click(detail.getByRole("button", { name: "Confirm" }));
    await expect(detail.getByRole("status").filter({ hasText: "Written to" })).toBeVisible({ timeout: 60_000 });
  }
  count.end();

  count.start("New entity: visitor and cards");
  await count.click(page.getByRole("link", { name: "Probes" }));
  await count.click(page.getByRole("link", { name: "New entity", exact: true }));
  await count.fill(page.getByLabel("Entity id"), "visitor_profile");
  await count.click(page.getByRole("button", { name: "Next: Root table" }));
  await count.select(page.getByRole("combobox", { name: "Root table" }), { label: "card_baseline_mariadb · visitor_profile" });
  await count.click(page.getByRole("button", { name: "Next: Locations" }));
  await count.click(page.getByRole("region", { name: "Follow a key" }).getByRole("button", { name: "Add cards" }));
  await expect(page.getByText("Every location passes its save checks.")).toBeVisible({ timeout: 60_000 });
  await count.click(page.getByRole("button", { name: "Next: Identifiers" }));
  await count.click(page.getByRole("button", { name: "Next: Preview" }));
  await count.click(page.getByRole("button", { name: "Check" }));
  await count.click(page.getByRole("button", { name: "Create visitor_profile" }));
  await expect(page.getByRole("status", { name: "Entity created" })).toContainText("visitor_profile was created", { timeout: 60_000 });
  count.end();

  // The card example's first check, in place of the draft's own `count(visitor_profile) >= 1`.
  count.start("New Validate probe");
  await count.click(page.getByRole("link", { name: "Probes" }));
  await count.click(page.getByRole("button", { name: "New probe" }));
  const form = page.getByRole("form", { name: "New probe" });
  await count.fill(form.getByLabel("Probe id"), "visitor_cards");
  await count.fill(form.getByLabel("Title"), "A visitor's cards");
  await count.select(form.getByLabel("Kind"), "validate");
  await count.click(form.getByRole("button", { name: "Start the draft" }));
  const yaml = page.getByLabel("Probe YAML");
  await count.click(yaml.getByText("count(visitor_profile) >= 1"));
  await count.press("End");
  await count.press("Shift+Home");
  await count.type("- expect: count(cards where status == 'active') <= 1");
  await expect(yaml).toContainText("- expect: count(cards where status == 'active') <= 1");
  await expect(yaml).not.toContainText("count(visitor_profile)");
  const save = page.getByRole("button", { name: "Save", exact: true });
  await expect(save).toBeEnabled({ timeout: 60_000 });
  await count.click(save);
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("button", { name: "Confirm" })).toBeEnabled({ timeout: 60_000 });
  await count.click(dialog.getByRole("button", { name: "Confirm" }));
  await expect(dialog.getByRole("status")).toContainText("Saved card_baseline/probes/visitor_cards.yaml", { timeout: 60_000 });
  await count.press("Escape");
  count.end();

  // VP00012931 has two active cards (dev/seed/PLANTED.md), so the first Validate fails, as it should.
  count.start("First Validate");
  await count.click(page.getByRole("link", { name: "Validate" }));
  await count.fill(page.getByLabel("Any ID"), "VP00012931");
  await count.select(page.getByLabel("Reason"), "incident");
  await count.click(page.getByRole("button", { name: "Validate" }));
  const result = page.getByRole("region", { name: "Run result" });
  await expect(result.getByText("Failed", { exact: true })).toBeVisible({ timeout: 120_000 });
  count.end();

  writeFileSync(out ?? "", JSON.stringify(count.segments, null, 2));
});
