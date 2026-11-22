// Author: @SaadZarook
import { defineConfig } from "@playwright/test";

// Every screen under owl serve's own Content-Security-Policy (#148): a mock build, served by
// dev/ci/serve_web_app.py through the same mounts as owl serve, nonce and all. Vite's servers send
// no policy, so the other suites can't catch a refused style. Needs uv and a built guide.
process.env["PROBEOWL_CSP_PORT"] ??= String(43000 + (process.pid % 2000));
const port = Number(process.env["PROBEOWL_CSP_PORT"]);
const outDir = `dist-csp/${port}`;

export default defineConfig({
  testDir: "e2e-csp",
  retries: 0,
  use: { baseURL: `http://127.0.0.1:${port}` },
  webServer: {
    command: `npm run build -- --outDir ${outDir} --emptyOutDir && uv run --project ../backend python ../dev/ci/serve_web_app.py --build ${outDir} --port ${port}`,
    env: { VITE_MOCK_API: "true" },
    url: `http://127.0.0.1:${port}/inspect`,
    reuseExistingServer: false,
    timeout: 180_000,
  },
});
