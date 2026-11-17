#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
mode="${1:-play}"
case "$mode" in
  play|--check|--acceptance) ;;
  *) echo 'Usage: bash start.sh [--check|--acceptance]' >&2; exit 2 ;;
esac

command -v python3 >/dev/null || { echo 'Python 3 is required.' >&2; exit 2; }
command -v docker >/dev/null || { echo 'Docker Engine and Compose v2 are required.' >&2; exit 2; }
docker compose version >/dev/null
docker info >/dev/null 2>&1 || { echo 'Start Docker Engine before running this command.' >&2; exit 2; }
python3 scripts/init-env.py
docker compose config --quiet
if [[ "$mode" == --check ]]; then
  echo 'Prerequisites and Compose configuration are ready.'
  exit 0
fi

docker compose up -d --build
python3 scripts/bootstrap.py
if [[ "$mode" == --acceptance ]]; then
  docker compose exec -T game sh -c 'cd /app && node --test /app/tests/*.test.mjs'
  python3 tests/acceptance.py
else
  exec python3 scripts/web.py
fi
