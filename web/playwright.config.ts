// Author: @SaadZarook
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  use: { baseURL: "http://127.0.0.1:4173" },
  webServer: {
    // Through env, not the command: the shell syntax does not work on Windows.
    command: "npm run build && npm run preview",
    env: { VITE_MOCK_API: "true" },
    url: "http://127.0.0.1:4173",
    reuseExistingServer: !process.env["CI"],
    timeout: 120_000,
  },
});
