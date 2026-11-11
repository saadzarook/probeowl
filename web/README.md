<!-- Author: @SaadZarook -->
# ProbeOwl web

React, TypeScript and Vite. The servers listen on 127.0.0.1 only, and both forward `/api` to
`owl serve` on `http://127.0.0.1:7878`, so the browser never needs CORS.

## Against the mocks (default for UI work)

```sh
cd web
npm ci
VITE_MOCK_API=true npm run dev   # http://127.0.0.1:5173, MSW answers /api
```

## Against `owl serve`

From the repository root:

```sh
cd backend
uv run owl store init
uv run owl serve --config ../examples      # API on http://127.0.0.1:7878
# in another terminal:
cd web && npm run dev                      # http://127.0.0.1:5173, /api proxied
```

`owl serve` refuses changes from other sites. It accepts the web app from Vite's dev and preview
servers on loopback without being told: `http://127.0.0.1` or `http://localhost`, ports 5173 and
4173. On any other port, tell it the address with `--web-origin` (or `PROBEOWL_WEB_ORIGINS`,
comma-separated; loopback origins only). The web app sends `X-ProbeOwl-Request: 1` on every request,
and the proxy uses `changeOrigin` so `Host` names `owl serve`. A 403 about an unknown address means
the web app runs on a port `owl serve` was not told about.

`owl serve` needs each binding's password: UAT reads `PROBEOWL_WALLET_DB_UAT`,
`PROBEOWL_LEDGER_DB_UAT` and `PROBEOWL_ACCESS_DB_UAT` from the environment, and Production reads
the keychain. See `backend/README.md` and `dev/README.md`. Databases are only available in CI, so
locally this mode is for the API's validation behaviour, not real lookups.

## Checks

| Command | What it does |
| --- | --- |
| `npm run lint`, `npm run typecheck` | Lint and types |
| `npm run test:e2e` | Smoke and axe tests against the mocks |
| `npm run test:e2e:live` | Every screen's first load, and the run screens, against `npm run preview` and a running `owl serve` (CI: `.github/workflows/e2e.yml`) |

The live tests expect the preview on `http://127.0.0.1:4173` and `owl serve` on 7878. To use other ports, for
example while the mock tests' preview holds 4173, set `PROBEOWL_API_URL` for `npm run preview` (where `/api`
goes, default `http://127.0.0.1:7878`) and `PROBEOWL_LIVE_URL` for the tests (default `http://127.0.0.1:4173`).
