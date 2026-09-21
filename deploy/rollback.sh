#!/usr/bin/env bash
# Roll back to the previous tag. Works only while migrations stay backward
# compatible for one release -- expand/contract, never a destructive drop in
# the same release that stops using a column.
set -euo pipefail
cd "$(dirname "$0")/.."
TARGET="${1:-}"
[[ -n "$TARGET" ]] || { echo "usage: rollback.sh <tag-or-sha>"; git log --oneline -10; exit 1; }
git checkout "$TARGET"
exec ./deploy/deploy.sh
