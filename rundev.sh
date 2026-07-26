#!/usr/bin/env bash
# Local dev launcher (not used in the container — docker/ carries the real runtime).
# Points the app at .devdata/ so a dev run never touches the real builds/db mounts.
set -e
cd "$(dirname "$0")"
export DB_DIR="$PWD/.devdata/db"
export BUILDS_ROOT="$PWD/.devdata/builds"
export LOG_DIR="$PWD/.devdata/logs"
export PYTHONIOENCODING=utf-8
mkdir -p "$DB_DIR" "$BUILDS_ROOT" "$LOG_DIR"
cd backend
exec python -m uvicorn app.main:app --host 127.0.0.1 --port 8099
