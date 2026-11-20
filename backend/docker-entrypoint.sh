#!/usr/bin/env sh
set -eu

# Migrations must run as the database owner so they can create policies and
# triggers. The API itself connects as the non-owner runtime role afterwards.
if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
  echo "Applying database migrations..."
  alembic upgrade head
fi

if [ "${SEED_DEMO:-false}" = "true" ]; then
  echo "Seeding reference data and demo fixtures..."
  python -m app.seed
elif [ "${SEED_REFERENCE:-true}" = "true" ]; then
  echo "Syncing reference data (permissions, roles, policy)..."
  python -m app.seed --reference
fi

exec "$@"
