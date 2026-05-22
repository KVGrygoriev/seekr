#!/usr/bin/env bash
# Entry script for the `app` service: apply migrations then exec the given command.
# Default command is `seekr serve`. Pass any args to override (e.g. `seekr run-once`).
set -euo pipefail

echo "[seekr] applying migrations..."
alembic upgrade head

if [ "$#" -eq 0 ]; then
  set -- seekr serve
fi

echo "[seekr] starting: $*"
exec "$@"
