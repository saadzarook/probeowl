#!/bin/bash
# Author: @SaadZarook
# Container hook: seeds on first start by running the portable script.
set -euo pipefail

exec /bin/bash /seed/oracle/seed.sh
