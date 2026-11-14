#!/bin/bash
# Author: @SaadZarook
# Copy the IBM Plex files the landing page uses into site/fonts/, with each family's OFL licence
# beside them, from the @fontsource packages web/ already installs (run npm ci in web/ first).
# The same files the wheel bundles for the web app and the guide, plus Plex Sans Condensed for the
# page's headings. Run it again after a font package is updated, and commit what it copies.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
SOURCE="$REPO/web/node_modules/@fontsource"
TARGET="$REPO/site/fonts"
[ -d "$SOURCE" ] || { echo "$SOURCE not found: run npm ci in web/ first" >&2; exit 1; }

# family package, then the weights the page uses (normal style, latin subset only)
FONTS=(
  "ibm-plex-sans 400 500 600 700"
  "ibm-plex-mono 400 500 600"
  "ibm-plex-sans-condensed 600 700"
)
rm -rf "$TARGET"
mkdir -p "$TARGET"
for entry in "${FONTS[@]}"; do
  read -r package weights <<<"$entry"
  for weight in $weights; do
    cp "$SOURCE/$package/files/$package-latin-$weight-normal.woff2" "$TARGET/"
  done
  cp "$SOURCE/$package/LICENSE" "$TARGET/OFL-$package.txt"
done
ls "$TARGET"
