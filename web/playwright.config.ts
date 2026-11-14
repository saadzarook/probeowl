// Author: @SaadZarook
import { defineConfig } from "@playwright/test";

// Each run builds and starts its own preview server on a port of its own, and never reuses one
// already running: two runs at once, or a stale server on 4173, can't share or swap a server.
// The port is chosen once, here, and handed to the workers through the environment.
process.env["PROBEOWL_E2E_PORT"] ??= String(41000 + (process.pid % 2000));
const port = Number(process.env["PROBEOWL_E2E_PORT"]);
const url = `http://127.0.0.1:${port}`;
// Its own build too, so one run never serves another's half-written files.
const outDir = `dist-e2e/${port}`;

export default defineConfig({
  testDir: "e2e",
  // Green on the first attempt or not at all: a flaky test is a failure to fix, not to retry.
  retries: 0,
  use: { baseURL: url },
  webServer: {
    // Through env, not the command: the shell syntax does not work on Windows.
    command: `npm run build -- --outDir ${outDir} --emptyOutDir && npm run preview -- --outDir ${outDir} --host 127.0.0.1 --port ${port} --strictPort`,
    env: { VITE_MOCK_API: "true" },
    url,
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
