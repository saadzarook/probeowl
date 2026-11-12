#!/bin/bash
# Author: @SaadZarook
# Container hook: seeds on first start by running the portable script.
set -euo pipefail

SEED_PASSWORD="$MARIADB_ROOT_PASSWORD" exec /bin/bash /seed/mariadb/seed.sh
