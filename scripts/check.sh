#!/usr/bin/env bash
# Preflight check — verify the local environment is ready to run / test.
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PASS=0; FAIL=0

ok()   { echo "  [ok]  $1"; ((PASS++)) || true; }
fail() { echo "  [!!]  $1"; ((FAIL++)) || true; }
info() { echo "        $1"; }

echo "==> Intercom QA — local env check"
echo ""

# .env
echo "[ .env ]"
if [[ -f "$ROOT/.env" ]]; then
  ok ".env exists"
  for var in INTERCOM_ACCESS_TOKEN WEB_SECRET_KEY; do
    if grep -q "^${var}=.\+" "$ROOT/.env" 2>/dev/null; then
      ok "$var is set"
    else
      fail "$var missing or empty in .env"
    fi
  done
else
  fail ".env not found — copy .env.example and fill in tokens"
fi

echo ""
echo "[ Python ]"
VENV="$ROOT/.venv/bin/activate"
if [[ -f "$VENV" ]]; then
  ok ".venv present"
  source "$VENV"
  if python -c "import intercom_summary" 2>/dev/null; then
    ok "intercom_summary package importable"
  else
    fail "package not installed — run: pip install -e '.[dev]'"
  fi
  if python -c "import pytest" 2>/dev/null; then
    ok "pytest available"
  else
    fail "pytest not installed — run: pip install -e '.[dev]'"
  fi
else
  fail ".venv missing — run: python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'"
fi

echo ""
echo "[ Frontend ]"
FRONTEND="$ROOT/src/intercom_summary/web/frontend"
if [[ -d "$FRONTEND/node_modules" ]]; then
  ok "node_modules present"
else
  fail "node_modules missing — run: cd $FRONTEND && npm install"
fi
if [[ -d "$FRONTEND/dist" ]]; then
  ok "dist/ built"
else
  info "dist/ not built (only needed for production; dev server uses Vite)"
fi

echo ""
echo "[ Ollama ]"
OLLAMA_URL="${OLLAMA_BASE_URL:-http://localhost:11434}"
if curl -sf "$OLLAMA_URL/api/tags" >/dev/null 2>&1; then
  ok "Ollama reachable at $OLLAMA_URL"
  MODEL="${OLLAMA_MODEL:-qwen2.5:14b}"
  if curl -sf "$OLLAMA_URL/api/tags" | grep -q "$MODEL" 2>/dev/null; then
    ok "Model $MODEL present"
  else
    fail "Model $MODEL not found — run: ollama pull $MODEL"
  fi
else
  fail "Ollama not reachable at $OLLAMA_URL — run: brew services start ollama"
fi

echo ""
echo "[ Ports ]"
if lsof -iTCP:8000 -sTCP:LISTEN -nP &>/dev/null; then
  fail "Port 8000 already in use (backend may already be running)"
else
  ok "Port 8000 free"
fi
if lsof -iTCP:5173 -sTCP:LISTEN -nP &>/dev/null; then
  fail "Port 5173 already in use (Vite may already be running)"
else
  ok "Port 5173 free"
fi

echo ""
if [[ $FAIL -eq 0 ]]; then
  echo "All checks passed ($PASS ok). Run ./scripts/dev.sh to start."
else
  echo "$FAIL issue(s) found, $PASS ok. Fix the items marked [!!] above."
  exit 1
fi
