// Author: @SaadZarook
import type { Page } from "@playwright/test";

// Every screen and state of the web app, and how to open one: the screen rules (screen-rules.spec.ts)
// and the strict-CSP tests (e2e-csp/) walk the same list.

export type Screen = { name: string; path: string; ready?: (page: Page) => Promise<void> };

export const SCREENS: Screen[] = [
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
  {
    name: "a healthy comparison",
    path: "/validate",
    ready: async (page) => {
      await page.getByLabel("Any ID").fill("tp_91f2c7");
      await page.getByLabel("Reason").selectOption("incident");
      await page.getByLabel(/^Reference/).fill("WALLET-482");
      await page.getByRole("button", { name: "Validate" }).click();
      const section = page.getByRole("region", { name: "Compare with a healthy record" });
      await section.getByRole("button", { name: "Newest passing" }).click();
      await section.getByText(/\d+ differences/).waitFor();
    },
  },
  {
    name: "an Inspect run",
    path: "/inspect",
    ready: async (page) => {
      await page.getByRole("button", { name: "Application" }).click();
      await page.getByRole("button", { name: /Card access/ }).last().click();
      await page.getByLabel("Identifier").fill("C204518");
      await page.getByLabel("Reason").selectOption("customer_complaint");
      await page.getByLabel(/^Reference/).fill("SUP-20931");
      await page.getByRole("button", { name: "Inspect", exact: true }).click();
      await page.getByRole("region", { name: "Inspect result" }).getByText("3 issues").waitFor();
    },
  },
  { name: "Health", path: "/health" },
  { name: "Probes", path: "/probes" },
  {
    name: "an open probe",
    path: "/probes",
    ready: async (page) => {
      await page.getByLabel("Open probe").selectOption("topup_completed");
      await page.getByRole("button", { name: "Save", exact: true }).waitFor();
    },
  },
  { name: "Knowledge base", path: "/knowledge-base" },
  { name: "Tables", path: "/tables" },
  { name: "Connectors", path: "/connectors" },
  { name: "Add connection", path: "/connectors/new" },
  { name: "Runs", path: "/runs" },
  {
    name: "Runs in UAT",
    path: "/runs",
    ready: async (page) => {
      await page.getByRole("main").getByLabel("Environment").or(page.getByLabel("Environment").first()).first().selectOption("uat");
      await page.getByText("Top-up completed").first().waitFor();
    },
  },
  { name: "Audit", path: "/audit" },
  { name: "New entity", path: "/probes/new-entity" },
  {
    name: "the entity graph with a REST lookup",
    path: "/probes/new-entity",
    ready: async (page) => {
      await page.getByRole("button", { name: "Application" }).click();
      await page.getByRole("button", { name: /Wallet/ }).last().click();
      await page.getByRole("combobox", { name: "Root table" }).selectOption({ label: "wallet_db · topups" });
      await page.getByLabel("Sample", { exact: true }).fill("tp_91f2c7");
      const rest = page.getByRole("group", { name: "REST lookup" });
      await rest.getByRole("button", { name: "Use checkout_api" }).click();
      await rest.getByLabel("Path under the base URL").fill("/payments");
      await rest.getByRole("button", { name: "Add the REST lookup" }).click();
      await page.getByRole("group", { name: "First REST request" }).getByRole("button", { name: "Check sample" }).click();
      await page.getByRole("listitem", { name: "Location payments" }).getByLabel("status", { exact: true }).waitFor();
    },
  },
  { name: "Setup", path: "/setup" },
];

export const VALIDATE: Screen = { name: "Validate", path: "/validate" };


export async function open(page: Page, screen: Screen, width: number): Promise<void> {
  await page.setViewportSize({ width, height: 900 });
  await page.goto(screen.path);
  await page.getByRole("main").waitFor();
  // Measured once its data has loaded: the mocks answer through the service worker, as requests.
  await page.waitForLoadState("networkidle");
  await screen.ready?.(page);
  await page.waitForLoadState("networkidle");
}
