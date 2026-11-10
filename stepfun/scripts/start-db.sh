#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -n "${SEEKDB_BIN:-}" ]]; then
  db_binary="$(cd "$(dirname "$SEEKDB_BIN")" && pwd)/$(basename "$SEEKDB_BIN")"
  # A short persistent path also avoids macOS's Unix-socket path length limit.
  task_db_root="${SEEKDB_BASE_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/local-notes-seekdb}"
  mkdir -p "$task_db_root"
  exec "$db_binary" --nodaemon --base-dir "$task_db_root" --port "${SEEKDB_PORT:-2881}" \
    --log-level ERROR --parameter datafile_size=1G --parameter datafile_maxsize=4G --parameter log_disk_size=2G
else
  exec docker compose -f compose.yaml up -d
fi
