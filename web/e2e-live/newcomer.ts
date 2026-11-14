// Author: @SaadZarook
import { expect, type Locator, type Page } from "@playwright/test";

// The newcomer path's counting rules (#27), shared by the v0.1 path (after owl init) and the v0.2
// path (first-run setup in the browser). Every step goes through the counter, so the count is
// what a person would do by hand:
// - action: a click, a choice in a select, a field filled, or a key that is not a character;
// - characters: every character typed into a field or the editor.

export interface Segment {
  name: string;
  actions: number;
  characters: number;
  seconds: number;
}

/** Counts what a person would do in each segment, and how long the machine took. */
export class Counter {
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

  /** A field filled with the password: one action, its characters left out, as in v0.1. */
  async fillPassword(target: Locator, password: string) {
    this.current().actions += 1;
    await target.fill(password);
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

/**
 * The part both paths share, from a connector with its labels confirmed to a first Validate: the
 * visitor entity with its cards, a Validate probe with the card example's check, and the run on
 * VP00012931, which fails as planted (two active cards, dev/seed/PLANTED.md).
 */
export async function entityProbeAndValidate(page: Page, count: Counter, connector: string) {
  count.start("New entity: visitor and cards");
  await count.click(page.getByRole("link", { name: "Probes" }));
  await count.click(page.getByRole("link", { name: "New entity", exact: true }));
  await count.fill(page.getByLabel("Entity id"), "visitor_profile");
  await count.click(page.getByRole("button", { name: "Next: Root table" }));
  await count.select(page.getByRole("combobox", { name: "Root table" }), { label: `${connector} · visitor_profile` });
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
  await expect(dialog.getByRole("status")).toContainText("probes/visitor_cards.yaml", { timeout: 60_000 });
  await count.press("Escape");
  count.end();

  count.start("First Validate");
  await count.click(page.getByRole("link", { name: "Validate" }));
  await count.fill(page.getByLabel("Any ID"), "VP00012931");
  await count.select(page.getByLabel("Reason"), "incident");
  await count.click(page.getByRole("button", { name: "Validate" }));
  const result = page.getByRole("region", { name: "Run result" });
  await expect(result.getByText("Failed", { exact: true })).toBeVisible({ timeout: 120_000 });
  count.end();
}
