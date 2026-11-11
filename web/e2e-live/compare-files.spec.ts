// Author: @SaadZarook
import { expect, test } from "@playwright/test";

// Made up: the test proves this value is masked.
const SECRET = "live-abc123-secret"; // gitleaks:allow

test("two real files: masked on screen and in the response, revealed only from the file", async ({ page }) => {
  await page.goto("/tools/compare-files");
  const responses: string[] = [];
  page.on("response", (r) => {
    if (r.url().includes("/compare")) void r.text().then((t) => responses.push(t));
  });
  await page.getByLabel("File A file").setInputFiles({
    name: "prod.yaml",
    mimeType: "text/yaml",
    buffer: Buffer.from(`payments:\n  api_key: ${SECRET}\n  max: 3\n  note: x\n`),
  });
  await page.getByLabel("File B file").setInputFiles({
    name: "uat.yaml",
    mimeType: "text/yaml",
    buffer: Buffer.from(`payments:\n  api_key: other-value-2\n  max: 5\n  note: ${SECRET}\n`),
  });
  await page.getByRole("button", { name: "Compare", exact: true }).click();

  const found = page.getByRole("region", { name: "Differences" });
  await expect(found.getByRole("row", { name: /payments\.max/ })).toContainText("5");
  await expect(found).not.toContainText(SECRET);
  await expect(page.getByRole("region", { name: "Side by side" })).not.toContainText(SECRET);
  await expect.poll(() => responses.length).toBeGreaterThan(0);
  expect(responses.join("")).not.toContain(SECRET);

  // The same secret under an ordinary key on the other side is masked too, and can be revealed.
  const note = found.getByRole("row", { name: /payments\.note/ });
  await expect(note).not.toContainText(SECRET);
  await note.getByRole("button", { name: "Reveal payments.note" }).click();
  await expect(note).toContainText(SECRET);
});
