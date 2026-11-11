// Author: @SaadZarook
import { defineConfig } from "@playwright/test";

// Runs against `npm run preview` talking to a real `owl serve`; CI starts both (e2e.yml).
export default defineConfig({
  testDir: "e2e-live",
  // One server, shared state: the kill switch test stops reads for the others while it runs.
  workers: 1,
  reporter: [["list"], ["html", { open: "never" }]],
  // PROBEOWL_LIVE_URL: a preview on another port, when 4173 is taken (the mock tests' preview uses it).
  use: { baseURL: process.env["PROBEOWL_LIVE_URL"] ?? "http://127.0.0.1:4173" },
});
