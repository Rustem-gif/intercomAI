#!/usr/bin/env bash
# Start backend (uvicorn --reload) + Vite frontend side-by-side.
# Ctrl-C kills both.
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$ROOT/.venv/bin/activate"
FRONTEND="$ROOT/src/intercom_summary/web/frontend"

if [[ ! -f "$VENV" ]]; then
  echo "ERROR: .venv not found at $ROOT/.venv"
  echo "       Run: python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'"
  exit 1
fi

if [[ ! -d "$FRONTEND/node_modules" ]]; then
  echo "ERROR: node_modules missing. Run: cd $FRONTEND && npm install"
  exit 1
fi

cleanup() {
  echo ""
  echo "Stopping…"
  kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
  wait "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
  echo "Done."
}
trap cleanup INT TERM

source "$VENV"
cd "$ROOT"

echo "==> Backend  →  http://127.0.0.1:8000"
uvicorn intercom_summary.web.api:app \
  --host 127.0.0.1 --port 8000 --reload \
  --log-level info &
BACKEND_PID=$!

echo "==> Frontend →  http://localhost:5173  (proxies /api → :8000)"
cd "$FRONTEND"
npm run dev &
FRONTEND_PID=$!

wait "$BACKEND_PID" "$FRONTEND_PID"
