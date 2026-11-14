#!/bin/bash
# Author: @SaadZarook
# Builds what GitHub Pages serves into one folder: the landing page and its fonts at the root, and
# the guide under guide/, built offline as the release wheel builds it (guide/hooks.py). Then
# checks it in a browser: no request leaves the site, no console error. From the repo root, after
# npm ci in web/ and npx playwright install chromium:
#   bash dev/site/build.sh <empty folder>
# Deploying is a separate step, by a person: see dev/site/deploy.sh.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
STAGE="${1:?usage: dev/site/build.sh <empty folder>}"
mkdir -p "$STAGE"
[ -z "$(ls -A "$STAGE")" ] || { echo "$STAGE is not empty" >&2; exit 1; }
STAGE="$(cd "$STAGE" && pwd)"

(cd "$REPO/guide" && NO_MKDOCS_2_WARNING=1 uvx -q --with-requirements requirements.txt --from mkdocs \
  mkdocs build --strict --clean -d "$STAGE/guide")
cp "$REPO/site/index.html" "$STAGE/index.html"
cp -r "$REPO/site/fonts" "$STAGE/fonts"
touch "$STAGE/.nojekyll"  # serve the files as they are

node "$REPO/dev/site/check.mjs" "$STAGE/index.html"
node "$REPO/dev/site/check_stage.mjs" "$STAGE"
echo "Built and checked: $STAGE"
