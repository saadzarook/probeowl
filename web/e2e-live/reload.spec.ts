// Author: @SaadZarook
import { expect, test } from "@playwright/test";

// Reload configuration against a real owl serve: the examples, loaded again, with nothing changed.
test("Reload configuration loads the folder again and says nothing changed", async ({ page }) => {
  await page.goto("/connectors");
  await page.getByRole("button", { name: "Reload configuration" }).click();
  const result = page.getByRole("status").filter({ hasText: "Reloaded:" });
  await expect(result).toContainText("Reloaded: 2 applications");
  await expect(result).toContainText("No application or connector changed.");
  // The screen still lists the connectors (Card access is chosen first), read again after the reload.
  await expect(page.getByRole("row", { name: /access_db/ })).toBeVisible();
});
