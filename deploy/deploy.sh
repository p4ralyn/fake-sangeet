#!/usr/bin/env bash
# Deploy on the VM: pull, rebuild, migrate, restart. ~20 seconds of downtime.
# Per LLD-v2 §12: do not run this on event day after 6am.
set -euo pipefail
cd "$(dirname "$0")/.."

[[ -f .env ]] || { echo "No .env on this host. See deploy/bootstrap-vm.sh."; exit 1; }

echo "==> pulling"
git pull --ff-only

echo "==> building"
docker compose build api web

echo "==> restarting"
docker compose up -d

echo "==> migrating"
docker compose exec -T api alembic upgrade head

echo "==> seeding (idempotent)"
docker compose exec -T api python /srv/scripts/seed.py

echo "==> health"
for i in $(seq 1 20); do
  if curl -fsS localhost:8000/readyz >/dev/null 2>&1; then
    curl -sS localhost:8000/readyz; echo; echo "==> deployed"; exit 0
  fi
  sleep 2
done
echo "!! /readyz never came up. docker compose logs api" >&2
exit 1
