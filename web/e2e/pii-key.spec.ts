// Author: @SaadZarook
import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

// An entity whose key is PII (#66): runs show its subject [masked], and what needs the real value
// later asks for it to be pasted. A pasted value is sent for that one action and kept nowhere.
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("mock-pii-key", "1"));
});

/** The URL, both storages and React Query's mutation cache: none may keep a pasted value. */
async function kept(page: Page): Promise<string> {
  return page.evaluate(() => {
    const mutations = (window as unknown as { probeowlMutations?: () => unknown[] }).probeowlMutations?.() ?? ["no hook"];
    return JSON.stringify([location.href, { ...localStorage }, { ...sessionStorage }, mutations]);
  });
}

const step = (page: Page, title: RegExp) => page.getByRole("region", { name: title });

test("a read step asks for the subject before it runs, and keeps it nowhere", async ({ page }) => {
  const bodies: string[] = [];
  page.on("request", (r) => {
    if (r.method() === "POST" && r.url().endsWith("/steps/check-outbox/run")) bodies.push(r.postData() ?? "");
  });
  await page.goto("/knowledge-base?run=run_demo");
  const read = step(page, /Step 1:/);
  const run = read.getByRole("button", { name: "Run read-only" });
  await expect(run).toBeDisabled();
  const field = read.getByLabel("This run's key is PII and stored masked: paste its ID to run this step");
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await field.fill("tp_91f2c7");
  await run.click();
  await expect(read.getByLabel("Result")).toContainText("Found");
  expect(bodies).toHaveLength(1);
  expect(JSON.parse(bodies[0] ?? "{}")).toEqual({ run_without_plan_check: false, subject: "tp_91f2c7" });
  expect(await kept(page)).not.toContain("tp_91f2c7");
});

test("a probe step asks for it too", async ({ page }) => {
  await page.goto("/knowledge-base?run=run_demo");
  const probe = step(page, /Step 6:/);
  const again = probe.getByRole("button", { name: /again$/ });
  await expect(again).toBeDisabled();
  await probe.getByLabel("This run's key is PII and stored masked: paste its ID to run this step").fill("tp_91f2c7");
  await again.click();
  await expect(probe.getByRole("status")).toContainText("Run run_");
  expect(await kept(page)).not.toContain("tp_91f2c7");
});

test("the healthy comparison asks for this run's ID to find a healthy record by itself", async ({ page }) => {
  await page.goto("/validate");
  await expect(page.getByLabel("Probe")).toHaveValue("topup_completed");
  await page.getByLabel("Any ID").fill("tp_91f2c7");
  await page.getByLabel("Reason").selectOption("incident");
  await page.getByLabel(/^Reference/).fill("WALLET-482");
  await page.getByRole("button", { name: "Validate" }).click();
  const section = page.getByRole("region", { name: "Compare with a healthy record" });
  const newest = section.getByRole("button", { name: "Newest passing" });
  await expect(newest).toBeDisabled();
  await section.getByLabel("This run's key is PII and stored masked: paste its ID to find a healthy record by itself").fill("tp_91f2c7");
  await newest.click();
  await expect(section.getByRole("columnheader", { name: "Failing [masked]" })).toBeVisible();
  // Once only: in the run the person typed into Validate. The pasted ID added none.
  expect((await kept(page)).match(/tp_91f2c7/g) ?? []).toHaveLength(1);
});

test("masked matches are picked by number, with the value pasted again", async ({ page }) => {
  await page.goto("/inspect");
  await page.getByRole("button", { name: "Application" }).click();
  await page.getByRole("button", { name: /Card access/ }).last().click();
  await expect(page.getByLabel("Probe")).toHaveValue("visitor_cards");
  await page.getByLabel("Identifier").fill("a.rahman@example.com");
  await page.getByLabel("Reason").selectOption("customer_complaint");
  await page.getByLabel(/^Reference/).fill("SUP-20931");
  await page.getByRole("button", { name: "Inspect", exact: true }).click();

  const ambiguous = page.getByRole("region", { name: "Ambiguous identifier" });
  await expect(ambiguous.getByText("Match 1", { exact: true })).toBeVisible();
  await expect(ambiguous.getByText("Match 2", { exact: true })).toBeVisible();
  await expect(ambiguous).not.toContainText("VP000");
  const second = ambiguous.getByRole("button", { name: "Inspect this one match 2" });
  await expect(second).toBeDisabled();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  // A different value finds other matches, so the numbers may point elsewhere: refused.
  await ambiguous.getByLabel("Paste the email you searched for again").fill("someone.else@example.com");
  await second.click();
  await expect(ambiguous.getByRole("alert")).toContainText("the matches have changed since this run: start again");
  await ambiguous.getByLabel("Paste the email you searched for again").fill("a.rahman@example.com");
  await second.click();
  await expect(page.getByRole("heading", { name: "Visitor cards on [masked]" })).toBeVisible();
  // Once only: in the run the person typed into Inspect. Pasting it again added none.
  expect((await kept(page)).match(/a\.rahman@example\.com/g) ?? []).toHaveLength(1);
});
