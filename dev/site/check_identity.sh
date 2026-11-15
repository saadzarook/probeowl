#!/bin/bash
# Author: @SaadZarook
# Whether an email may sign the gh-pages commit deploy.sh pushes: only a GitHub noreply address,
# so a deploy from a fresh clone never offers a private email, which GitHub refuses to push for an
# account that keeps its email private (#104).
#   bash dev/site/check_identity.sh <email>
set -euo pipefail
email="${1:-}"
case "$email" in
  *@users.noreply.github.com) exit 0 ;;
esac
echo "deploy.sh commits as '${email:-no email}', which is not a GitHub noreply address." >&2
echo "Set one in this clone first: git config --local user.email <id>+<login>@users.noreply.github.com" >&2
exit 1
