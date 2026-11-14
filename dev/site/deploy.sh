#!/bin/bash
# Author: @SaadZarook
# Publishes a folder built and checked by dev/site/build.sh to the gh-pages branch, which GitHub
# Pages serves. The branch holds only the built site, so it is replaced each time. Run by a
# person, from the repo root, with the commit the folder was built from:
#   bash dev/site/deploy.sh <built folder>
# Then check the live site: node dev/site/check_stage.mjs https://saadzarook.github.io/probeowl/
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
STAGE="$(cd "${1:?usage: dev/site/deploy.sh <built folder>}" && pwd)"
[ -f "$STAGE/index.html" ] && [ -f "$STAGE/guide/index.html" ] && [ -f "$STAGE/.nojekyll" ] \
  || { echo "$STAGE is not a folder built by dev/site/build.sh" >&2; exit 1; }
COMMIT="$(git -C "$REPO" rev-parse --short HEAD)"

cd "$STAGE"
rm -rf .git
git init -q -b gh-pages
git add -A
git -c user.name="$(git -C "$REPO" config user.name)" -c user.email="$(git -C "$REPO" config user.email)" \
  commit -q -m "Site from $(git -C "$REPO" rev-parse --abbrev-ref HEAD) $COMMIT"
git push --force https://github.com/saadzarook/probeowl.git gh-pages
echo "Pushed. Pages takes a minute or two; then check the live site."
