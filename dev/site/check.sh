#!/bin/bash
# Author: @SaadZarook
# The landing page's checks (site/index.html), run in CI on any change to site/ and before a
# deploy: no external URL in src, href or url() but outbound links, a CSP that allows no other
# origin and pins the inline script by hash, the fonts and their licences present, and the page
# fitting at 390 px in light and dark with no request leaving the site. From the repo root, after
# npm ci in web/ and npx playwright install chromium.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
node "$REPO/dev/site/check.mjs" "$@"
